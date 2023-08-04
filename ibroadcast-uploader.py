#!/usr/bin/env python3

import requests
import json
import glob
import os
import hashlib
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor

class ServerError(Exception):
    pass

class ValueError(Exception):
    pass

class Uploader(object):
    """
    Class for uploading content to iBroadcast.
    """

    VERSION = '0.5'
    CLIENT = 'python 3 uploader script'
    DEVICE_NAME = 'python 3 uploader script'
    USER_AGENT = 'ibroadcast-uploader/' + VERSION

    def __init__(self, login_token, directory, no_cache, verbose, silent, skip_confirmation, parallel_uploads):
        self.login_token = login_token
        self.directory = directory
        self.no_cache = no_cache
        self.verbose = verbose
        self.silent = silent
        self.skip_confirmation = skip_confirmation
        self.parallel_uploads = parallel_uploads

        self.supported = None
        self.files = []
        self.skipped_files = []
        self.failed_files = []
        self.md5_int = {}
        self.md5_ext = {}

        if self.verbose:
            sys.tracebacklimit = 1000

    def process(self):
        try:
            self.login()
            self.get_supported_types()
        except (ServerError, ValueError) as e:
            print(e)
            return

        if not self.silent:
            print('Building file list...')
        self.load_files()

        if self.confirm():
            self.check_md5()
            self.prepare_upload()

    def login(self):
        post_data = json.dumps({
            'mode': 'login_token',
            'login_token': self.login_token,
            'app_id': 1007,
            'type': 'account',
            'version': self.VERSION,
            'client': self.CLIENT,
            'device_name': self.DEVICE_NAME,
            'user_agent': self.USER_AGENT
        })
        response = requests.post(
            "https://api.ibroadcast.com/s/JSON/",
            data=post_data,
            headers={'Content-Type': 'application/json', 'User-Agent': self.USER_AGENT}
        )

        if not response.ok:
            raise ServerError('Server returned bad status:', response.status_code)

        jsoned = response.json()

        if 'user' not in jsoned:
            raise ValueError(jsoned.get('message', 'Login failed'))

        if self.verbose:
            print('Login successful - user_id:', jsoned['user']['id'])

        self.user_id = jsoned['user']['id']
        self.token = jsoned['user']['token']

    def get_supported_types(self):
        post_data = json.dumps({
            'mode': 'status',
            'user_id': self.user_id,
            'token': self.token,
            'supported_types': 1,
            'version': self.VERSION,
            'client': self.CLIENT,
            'device_name': self.DEVICE_NAME,
            'user_agent': self.USER_AGENT
        })
        response = requests.post(
            "https://api.ibroadcast.com/s/JSON/",
            data=post_data,
            headers={'Content-Type': 'application/json', 'User-Agent': self.USER_AGENT}
        )

        if not response.ok:
            raise ServerError('Server returned bad status:', response.status_code)

        jsoned = response.json()

        if 'user' not in jsoned:
            raise ValueError(jsoned.get('message', 'Unable to fetch account info'))

        if self.verbose:
            print('Account info fetched')

        self.supported = [filetype['extension'] for filetype in jsoned.get('supported', [])]

    def load_files(self, directory=None):
        if self.supported is None:
            raise ValueError('Supported not yet set - have you logged in yet?')

        if not directory:
            directory = self.directory or os.getcwd()

        for full_filename in glob.glob(os.path.join(directory, '*')):
            filename = os.path.basename(full_filename)
            if filename.startswith('.'):
                continue

            dummy, ext = os.path.splitext(full_filename)
            if ext in self.supported:
                self.files.append(full_filename)

            if os.path.isdir(full_filename):
                self.load_files(full_filename)

    def confirm(self):
        if self.skip_confirmation:
            return True
        else:
            print("Found %s files.  Press 'L' to list, or 'U' to start the " \
                  "upload." % len(self.files))
            response = self.get_input('--> ')

            print()
            if response == 'L'.upper():
                print('Listing found, supported files')
                for filename in sorted(self.files):
                    print(' -', filename)
                print()
                print("Press 'U' to start the upload if this looks reasonable.")
                response = self.get_input('--> ')
            if response == 'U'.upper():
                if self.verbose:
                    print('Starting upload.')
                return True

            if self.verbose:
                print('Aborting')
            return False

    def get_input(self, inp):
        if sys.version_info >= (3, 0):
            return input(inp)
        else:
            return raw_input(inp)

    def calcmd5(self, filePath="."):
        with open(filePath, 'rb') as fh:
            m = hashlib.md5()
            while True:
                data = fh.read(8192)
                if not data:
                    break
                m.update(data)
        return m.hexdigest()

    def check_md5(self):
        self.__load_md5_int()
        self.__load_md5_ext()

        print_filename_again = True
        current_path = ''
        file_list = self.files[:]

        if not self.verbose and not self.silent:
            file_list = self.progressbar(self.files[:], "Calculating MD5 hashes:", 60)

        for filename in file_list:
            file_base_name = ' "' + os.path.basename(filename) + '"'
            if not self.silent and self.verbose:
                if os.path.dirname(filename) != current_path:
                    current_path = os.path.dirname(filename)
                    print('\nChecking directory %s...' % current_path)

            if (not self.no_cache) and filename in self.md5_int:
                file_md5 = self.md5_int[filename]
            else:
                print_filename_again = False
                if not self.silent and self.verbose:
                    print('Calculating MD5 for file%s...' % file_base_name, end='')
                file_md5 = self.calcmd5(filename)
                self.md5_int[filename] = file_md5

            if file_md5 in self.md5_ext:
                self.skipped_files.append(filename)
                if not self.silent and self.verbose:
                    if not print_filename_again:
                        file_base_name = ""
                    print('Skipping%s, already uploaded.' % file_base_name)
                self.files.remove(filename)
            elif not self.silent and self.verbose:
                if not print_filename_again:
                    print()
                    continue
                print('The MD5 for%s is cached, but the file has not been uploaded yet' % file_base_name)
            print_filename_again = True

        self.save_md5_int()

    def save_md5_int(self):
        with open(self.md5_int_path, 'w') as fp:
            json.dump(self.md5_int, fp, indent=2)

    def __load_md5_int(self):
        self.md5_int_path = os.path.expanduser('~') + '/.ibroadcast_md5s'

        if os.path.exists(self.md5_int_path):
            with open(self.md5_int_path) as json_file:
                self.md5_int = json.load(json_file)

    def __load_md5_ext(self):
        post_data = "user_id=%s&token=%s" % (self.user_id, self.token)
        response = requests.post(
            "https://upload.ibroadcast.com",
            data=post_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        if not response.ok:
            raise ServerError('Server returned bad status:', response.status_code)

        jsoned = response.json()
        self.md5_ext = jsoned['md5']

    def progressbar(self, it, prefix="", size=60, out=sys.stdout):
        count = len(it)
        def show(j):
            x = int(size*j/count)
            print("{}[{}{}] {}/{}".format(prefix, "#"*x, "."*(size-x), j, count),
                  end='\r', file=out, flush=True)
        if not self.verbose and not self.silent:
            show(0)
        for i, item in enumerate(it):
            yield item
            if not self.verbose and not self.silent:
                show(i+1)
        if not self.verbose and not self.silent:
            print("\n", flush=True, file=out)

    def prepare_upload(self):
        threads = self.parallel_uploads
        if threads == 0:
            threads = 1

        total_files = len(self.files)
        not_skipped_files = len(self.files)

        if not_skipped_files > 0:
            with ThreadPoolExecutor(max_workers=threads) as exe:
                for i in range(not_skipped_files):
                    exe.submit(self.upload, self.files[i])
                exe.shutdown(wait=True)

        skipped = len(self.skipped_files)
        failed = len(self.failed_files)
        uploaded = max(total_files - skipped - failed, 0)
        print('Uploaded/Skipped/Failed/Total: %s/%s/%s/%s.' % (uploaded, skipped, failed, total_files))

    def upload(self, filename):
        if not self.silent:
            print('Uploading:', filename)

        upload_file = open(filename, 'rb')

        file_data = {
            'file': upload_file,
        }

        post_data = {
            'user_id': self.user_id,
            'token': self.token,
            'file_path': filename,
            'method': self.CLIENT,
        }

        response = requests.post(
            "https://upload.ibroadcast.com",
            post_data,
            files=file_data,
        )

        upload_file.close()

        if not response.ok:
            raise ServerError('Server returned bad status:', response.status_code)
        jsoned = response.json()
        result = jsoned['result']

        if result is False:
            self.failed_files.append(filename)
            raise ValueError('File upload failed.')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Run this script in the parent directory of your music files. To acquire a login token, enable the \"Simple Uploaders\" app by visiting https://ibroadcast.com, logging in to your account, and clicking the \"Apps\" button in the side menu.\n")

    parser.add_argument('login_token', type=str, help='Login token')
    parser.add_argument('directory', type=str, nargs='?', help='Use this directory instead of the current one')
    parser.add_argument('-n', '--no-cache', action='store_true', help='Do not use local MD5 cache')
    parser.add_argument('-v', '--verbose', action='store_true', help='Be verbose')
    parser.add_argument('-p', '--parallel-uploads', type=int, nargs='?', const=3, default=3, choices=range(0,6), metavar='0-6', help='Number of parallel uploads, 3 by default.')
    parser.add_argument('-s', '--silent', action='store_true', help='Be silent')
    parser.add_argument('-y', '--skip-confirmation', action='store_true', help='Skip confirmation dialogue')

    args = parser.parse_args()
    uploader = Uploader(args.login_token, args.directory, args.no_cache, args.verbose, args.silent, args.skip_confirmation, args.parallel_uploads)

    uploader.process()
