#!/usr/bin/env python

import errno
import os
import requests
import requests.exceptions as rex
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from functools import partial
from getpass import getpass
from hashlib import md5
from requests.packages.urllib3.exceptions import InsecureRequestWarning
from twitter.common import app, log

from starsystem import constants


class RequestError(Exception):
    pass


class SubsonicError(Exception):
    pass


class SyncFileError(Exception):
    pass


def configure_app(app):
    """ Register the application's options, set usage, and configure submodules. """

    app.set_name('starsystem')

    app.set_usage("{} [opts]\nOptions marked with * are required.".format(app.name()))

    app.add_option('-i', '--uri', dest='subsonic_uri',
                   help='* URI of the Subsonic server.')
    app.add_option('-u', '--user', dest='username',
                   help='* Username on the specified Subsonic server.')
    app.add_option('-t', '--token', dest='token',
                   help='* API token for the given username/salt combination\n'
                        'See: http://www.subsonic.org/pages/api.jsp')
    app.add_option('-s', '--salt', dest='salt',
                   help='* Salt used to generate the API token.')
    app.add_option('-p', '--path', dest='download_path',
                   help='* Path to the directory whither songs will be downloaded.')
    app.add_option('-S', '--since', dest='since', type='date',
                   help='Collect all songs since the specified date.')
    app.add_option('-I', '--insecure', dest='insecure', default=False, action="store_true",
                   help='Don\'t verify SSL certificates. Verification is enabled by default.')
    app.add_option('-g', '--gen-token-interactive', dest='gen_token', default=False,
                   action="store_true", help='Generate an API token interactively.')

    app.set_option('twitter_common_log_disk_log_level', 'NONE', force=True)


def required_options_present(options, option_values):
    """
    Check for the presence of required options, with the side effect of
    logging missing options.
    """
    missing_options = []
    for option in sorted(options, key=lambda option: option.get_opt_string()):
        if option.dest is not None:
            option_value = getattr(option_values, option.dest)
            if option.help.startswith('*') and option_value in (None, ''):
                missing_options.append(option)
    if len(missing_options) > 0:
        for option in missing_options:
            log.error('Required option is missing: {}'.format(option.get_opt_string()))
        return False
    else:
        return True


def generate_token_interactive():
    password = getpass('Enter your Subsonic password: ')
    salt = getpass('Enter a salt (an integer of at least six digits): ')
    if len(salt) < 6 or not salt.isdigit():
        app.error('Salt value is not an integer of at least six digits.')
    token = md5(password + salt).hexdigest()
    print 'Your API token is: {}'.format(token)
    print 'This must be used with the same salt value entered during this session.'


def get_sync_file_path(download_path):
    return os.path.join(download_path, constants.SYNC_FILE_NAME)


def read_time_struct_from_sync_file(sync_file_path):
    try:
        with open(sync_file_path, 'r') as sync_file:
            try:
                return time.gmtime(float(sync_file.readline().strip()))
            except ValueError as e:
                reraise_as_exception_type(SyncFileError, e)
    except EnvironmentError as e:
        reraise_as_exception_type(SyncFileError, e)


def write_time_struct_to_sync_file(sync_file_path, time_struct):
    try:
        with open(sync_file_path, 'w') as sync_file:
            try:
                sync_file.write(str(time.mktime(time_struct)))
            except TypeError as e:
                reraise_as_exception_type(SyncFileError, e)
    except EnvironmentError as e:
        reraise_as_exception_type(SyncFileError, e)


def get_start_date(download_path, starred_songs, songs_sorted=False, since=None):
    """
    Find the most recent starred date of synced songs in the download path.

    This will be explicitly stored in a file, but if that's missing
    it can be calculated from the directory contents.

    Since overrides other dates if present.

    Return epoch time if there are no valid results otherwise.
    """
    # Use since if present
    if since is not None:
        return time.strptime(str(since), '%Y-%m-%d')
    # Try to get most recent sync date from sync file
    try:
        return read_time_struct_from_sync_file(get_sync_file_path(download_path))
    except SyncFileError:
        pass
    download_path_files = {os.path.join(os.path.relpath(path, download_path), filename)
                           for path, _, filenames in os.walk(download_path)
                           for filename in filenames}
    synced_songs = [song for song in starred_songs if song.get('path') in download_path_files]
    # don't waste time if songs are already sorted
    get_most_recently_starred = (
        lambda x: x[-1] if songs_sorted else partial(max, key=song_to_starred_time_struct))
    if len(synced_songs) > 0:
        return song_to_starred_time_struct(get_most_recently_starred(synced_songs))
    else:
        return time.gmtime(0)


def song_to_starred_time_struct(song):
    """ Take a song, return the starred date in the form of a time module 9-tuple. """
    try:
        return time.strptime(song.get('starred', ''), '%Y-%m-%dT%H:%M:%S.%fZ')
    except ValueError:
        return time.gmtime(0)


def handle_request(f, validate_json=True):
    """ Run a function that generates a Requests.Response and handle exceptions. """
    try:
        response = f()
        response.raise_for_status()
        if validate_json:
            err = response.json()['subsonic-response'].get('error')
            if err is not None:
                raise SubsonicError('Error code {}: {}'.format(err['code'], err['message']))
        return response
    except (rex.RequestException, SubsonicError) as e:
        reraise_as_exception_type(RequestError, e)


def create_directory_if_missing_from_path(path):
    """ Create the directories necessary to use the full path specified. """
    dirpath = os.path.dirname(path)
    # first check if it's already a directory, cheaper than try/except
    if not os.path.isdir(dirpath):
        try:
            os.makedirs(dirpath)
        except OSError as e:
            if e.errno != errno.EEXIST and not os.path.isdir(dirpath):
                # path exists and isn't a directory
                raise


def reraise_as_exception_type(cls, exception):
    """ Reraise an exception as the specified type, preserving the original traceback. """
    exception_class, _, traceback = sys.exc_info()
    msg = 'Caught exception of type {}: {}'.format(exception_class, exception)
    raise cls, cls(msg), traceback


@contextmanager
def temporary_subdirectory(parent_directory):
    path = tempfile.mkdtemp(dir=parent_directory)
    try:
        yield path
    finally:
        try:
            shutil.rmtree(path)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


@contextmanager
def open_tempfile_with_atomic_write_to(path, **kwargs):
    """
    Open a temporary file object that atomically moves to the specified
    path upon exiting the context manager.

    Supports the same function signature as `open`.

    The parent directory exist and be user-writable.

    WARNING: This is just like 'mv', it will clobber files!
    """
    parent_directory = os.path.dirname(path)
    _tempfile = tempfile.NamedTemporaryFile(delete=False, dir=parent_directory)
    _tempfile.close()
    tempfile_path = _tempfile.name
    try:
        with open(tempfile_path, **kwargs) as file:
            yield file
            file.flush()
            os.fsync(file.fileno())
        os.rename(tempfile_path, path)
    finally:
        try:
            os.remove(tempfile_path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                pass
            else:
                raise e


def main(args, options):
    # Requests vendors its own urllib3, which emits annoying messages
    # when using insecure mode
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    if len(args) != 0:
        app.help()

    if options.gen_token:
        generate_token_interactive()
        app.shutdown(0)

    # Kind of a hack, but whatever
    option_definitions = app.Application.active()._main_options

    if not required_options_present(option_definitions, options):
        app.help()

    download_path = os.path.expanduser(options.download_path)

    base_params = dict(
        t = options.token,
        u = options.username,
        s = options.salt,
        c = app.name(),
        f = 'json',
        v = constants.API_VERSION)

    session = requests.Session()
    session.params.update(base_params)
    session.verify = not options.insecure

    # Get starred songs
    try:
        get_starred_response = handle_request(
            lambda: session.get("https://localhost:4041/rest/getStarred.view"))
    except RequestError as e:
        log.error("Bad response from Subsonic while fetching starred songs list:\n{}".format(e))
        raise

    # Extract songs from response
    try:
        starred_songs = filter(lambda song: song['contentType'].startswith('audio'),
                               get_starred_response.json()['subsonic-response']['starred']['song'])
    except (KeyError, ValueError) as e:
        reraise_as_exception_type(RequestError, e)

    # Do nothing if no songs are starred
    if len(starred_songs) == 0:
        app.shutdown(0)

    # Sort the songs by starred date so we can sync them in chronological order
    sorted_starred_songs = sorted(starred_songs, key=song_to_starred_time_struct)
    start_date = get_start_date(download_path, sorted_starred_songs, songs_sorted=True,
                                since=options.since)
    sync_file_path = get_sync_file_path(download_path)

    # Sync each song in chronological order by starred date
    for song in sorted_starred_songs:
        song_full_path = os.path.join(download_path, song['path'])
        if song_to_starred_time_struct(song) >= start_date and not os.path.exists(song_full_path):
            create_directory_if_missing_from_path(song_full_path)
            try:
                download_params = {'id': song['id']}
                download_response = handle_request(
                    lambda: session.get("https://localhost:4041/rest/download.view",
                                        params=download_params),
                    validate_json=False)
            except RequestError as e:
                log.error("Failed downloading the following song: {}\n{}".format(song['path'], e))
                raise
            with open_tempfile_with_atomic_write_to(song_full_path, mode='wb') as download_file:
                download_file.write(download_response.content)
            starred_date = song_to_starred_time_struct(song)
            if starred_date != time.gmtime(0):
                sync_file_is_stale = True
                # Try to read most recent sync date from sync file.
                try:
                    if read_time_struct_from_sync_file(sync_file_path) > starred_date:
                        sync_file_is_stale = False
                except SyncFileError:
                    pass
                # Write starred date of downloaded file if newer than existing date.
                if sync_file_is_stale:
                    try:
                        write_time_struct_to_sync_file(sync_file_path, starred_date)
                    except SyncFileError:
                        pass
