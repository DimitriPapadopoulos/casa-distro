# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function

import fnmatch
import getpass
from glob import glob
import json
import os
import os.path as osp
import re
import shutil
import socket
import subprocess
import sys
import time

from casa_distro.six.moves import shlex_quote

from casa_distro import (share_directories,
                         singularity,
                         vbox)
from casa_distro.web import url_listdir, urlopen
from casa_distro import downloader


bv_maker_branches = {
    'latest_release': 'latest_release',
    'master': 'master',
    'bug_fix': 'master',
    'integration': 'integration',
    'trunk': 'integration',
}

# We need to duplicate this function to allow copying over
# an existing directory


def copytree(src, dst, symlinks=False, ignore=None):
    """Recursively copy a directory tree using copy2().
    If exception(s) occur, an Error is raised with a list of reasons.
    If the optional symlinks flag is true, symbolic links in the
    source tree result in symbolic links in the destination tree; if
    it is false, the contents of the files pointed to by symbolic
    links are copied.
    The optional ignore argument is a callable. If given, it
    is called with the `src` parameter, which is the directory
    being visited by copytree(), and `names` which is the list of
    `src` contents, as returned by os.listdir():
        callable(src, names) -> ignored_names
    Since copytree() is called recursively, the callable will be
    called once for each directory that is copied. It returns a
    list of names relative to the `src` directory that should
    not be copied.
    XXX Consider this example code rather than the ultimate tool.
    """
    if os.path.isdir(src):
        names = os.listdir(src)
        dstnames = names
    else:
        names = [os.path.basename(src)]
        src = os.path.dirname(src)

        if os.path.isdir(dst):
            dstnames = names
        else:
            dstnames = [os.path.basename(dst)]
            dst = os.path.dirname(dst)

    if ignore is not None:
        ignored_names = ignore(src, names,
                               dst, dstnames)
    else:
        ignored_names = set()

    if not os.path.exists(dst):
        os.makedirs(dst)

    errors = []
    for name, new_name in zip(names, dstnames):
        if name in ignored_names:
            continue
        srcname = os.path.join(src, name)
        dstname = os.path.join(dst, new_name)
        try:
            if symlinks and os.path.islink(srcname):
                linkto = os.readlink(srcname)
                os.symlink(linkto, dstname)
            elif os.path.isdir(srcname):
                copytree(srcname, dstname, symlinks, ignore)
            else:
                # Will raise a SpecialFileError for unsupported file types
                shutil.copy2(srcname, dstname)
        # catch the Error from the recursive copytree so that we can
        # continue with other files
        except shutil.Error as err:
            errors.extend(err.args[0])
        except EnvironmentError as why:
            errors.append((srcname, dstname, str(why)))
    try:
        shutil.copystat(src, dst)
    except OSError as why:
        if (shutil.WindowsError is not None
                and isinstance(why, shutil.WindowsError)):
            # Copying file access times may fail on Windows
            pass
        else:
            errors.append((src, dst, str(why)))
    if errors:
        raise shutil.Error(errors)


def cp(src, dst, not_override=[], verbose=None):

    def override_exclusion(cur_src, names,
                           cur_dst, dst_names):
        excluded = []
        for n in not_override:
            if n in names:
                i = names.index(n)
                d = os.path.join(cur_dst, dst_names[i])
                if os.path.exists(d) or os.path.islink(d):
                    excluded.append(n)
                    if verbose:
                        print('file', d, 'exists,', 'skipping override.',
                              file=verbose)

        return excluded

    copytree(src, dst, ignore=override_exclusion)


def string_to_byte_count(size):
    coefs = {
        '': 1,
        'K': 1024,
        'M': 1024 ** 2,
        'G': 1024 ** 3}

    match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMG]?)$', size)
    if not match:
        raise ValueError('Invlid file system size: {0}'.format(size))
    n = float(match.group(1))
    coef = coefs[match.group(2)]
    return int(n * coef)


def update_config(config, update):
    """
    Update a configuration dictionary with an update configuration dictionary.
    These two dictionaries are JSON objects. This simply merges the
    two dictionaries (dictionaries are merged recursively, lists are
    concatenated)
    """
    for k, v in update.items():
        if k not in config:
            config[k] = v
        else:
            oldv = config[k]
            if isinstance(oldv, dict):
                update_config(oldv, v)
            elif isinstance(oldv, list):
                oldv += v
            else:
                config[k] = v


def find_in_path(file):
    '''Look for a file in directories of the ``PATH`` environment variable.
    '''
    path = os.environ.get('PATH').split(os.pathsep)
    for i in path:
        p = osp.normpath(osp.abspath(i))
        if p:
            r = glob(osp.join(p, file))
            if r:
                return r[0]


def iter_distros():
    """
    Iterate over all available distros. For each one, yield a
    dictionary corresponding to the casa_distro.json file
    with the "directory" item added.
    """
    for share_directory in share_directories():
        distro_dir = osp.join(share_directory, 'distro')
        if not osp.isdir(distro_dir):
            continue
        for basename in os.listdir(distro_dir):
            casa_distro_json = osp.join(distro_dir, basename,
                                        'casa_distro.json')
            if osp.isfile(casa_distro_json):
                with open(casa_distro_json) as f:
                    distro = json.load(f)
                distro['directory'] = osp.join(distro_dir, basename)
                yield distro


def select_distro(distro):
    """
    Select a distro given its name or an existing distro directory.
    """
    for d in iter_distros():
        if d['name'] == distro:
            return d
    if osp.isdir(distro):
        directory = distro
        casa_distro_json = osp.join(
            directory, 'conf', 'casa_distro.json')
        if osp.exists(casa_distro_json):
            distro = json.load(open(casa_distro_json))
            distro['directory'] = directory
            return distro
    raise ValueError('Invalid distro: {0}'.format(distro))


_casa_distro_directory = None


def casa_distro_directory():
    """
    Return the defaut casa_distro directory.
    Either $CASA_BASE_DIRECTORY or ~/casa_distro.
    """
    global _casa_distro_directory

    if _casa_distro_directory is None:
        _casa_distro_directory = os.environ.get('CASA_BASE_DIRECTORY')
        if not _casa_distro_directory:
            _casa_distro_directory = osp.expanduser('~/casa_distro')
    return _casa_distro_directory


def user_config_filename():
    """
    Get the user configuration file for casa-distro. This user config is
    outside of environments in order to allow configuration of read-only shared
    environments.
    """
    xdg_config_home = os.environ.get('XDG_CONFIG_HOME', '')
    if not xdg_config_home:
        xdg_config_home = osp.expanduser('~/.config')
    user_config_file = osp.join(xdg_config_home,
                                'casa-distro', 'casa_distro_3.json')
    return user_config_file


def iter_environments(base_directory=casa_distro_directory(), **filter):
    """
    Iterate over environments created with "setup" or "setup_dev" commands
    in the given
    base directory. For each one, yield a dictionary corresponding to the
    casa_distro.json file with the "directory" item added.
    """
    casa_distro_jsons = glob(osp.join(base_directory, '*',
                                      'conf', 'casa_distro.json'))
    if not casa_distro_jsons:
        # Special case where base_directroy is the directory of an environment
        casa_distro_jsons = glob(osp.join(base_directory, 'conf',
                                          'casa_distro.json'))
    for casa_distro_json in sorted(casa_distro_jsons):
        environment_config = json.load(open(casa_distro_json))
        directory = osp.dirname(osp.dirname(casa_distro_json))
        config = {}
        config['config_files'] = [casa_distro_json]
        config['directory'] = directory
        config['mounts'] = {
            '/casa/host': '{directory}',
            '/host': '/',
        }
        if 'WSL_DISTRO_NAME' in os.environ:
            # On Winows/WSL2, /dev/shm is a symlink to /run/shm. This
            # is supposed to be a directory like device stored in memory.
            # To avoid failur of some programs, /run/shm is mounted as
            # /tmp so that it behave like a writable directory as expected.
            config['mounts']['/run/shm'] = '/tmp'
        config['env'] = {
            'CASA_ENVIRONMENT': '{name}',
            'CASA_SYSTEM': '{system}',
            'CASA_HOST_DIR': '{directory}',
            'CASA_DISTRO': '{distro}',
        }
        if 'bv_maker_branch' in config:
            config['env']['CASA_BRANCH'] = config['bv_maker_branch']
        if environment_config['container_type'] == 'singularity':
            config.setdefault('gui_env', {}).update({
                'DISPLAY': '$DISPLAY'
            })

        update_config(config, environment_config)

        user_config_file = user_config_filename()
        for additional_config_file in [user_config_file]:
            if osp.exists(additional_config_file):
                config['config_files'].append(additional_config_file)
                with open(additional_config_file) as f:
                    update_config(config, json.load(f))

        match = False
        for k, p in filter.items():
            if p is None:
                continue
            v = config.get(k)
            if v is None or not fnmatch.filter([v], p):
                break
        else:
            match = True
        if match:
            yield config


def get_environment_image(config, base_directory=casa_distro_directory()):
    """
    Get the container image actually used by the given environment.
    It may be an updated version of the initial image.
    """
    image = config['image']
    image_id = config.get('image_id')
    if image_id:
        images = list(iter_images(base_directory=base_directory,
                                  type='run',
                                  image_id=image_id))
        if images:
            return images[0]
    images = list(iter_images(
            base_directory=base_directory,
            type=config.get('type'),
            image_version=config.get('image_version'),
            container_type=config.get('container_type'),
            system=config.get('system')))
    img_confs = {}
    for image in images:
        with open('%s.json' % image[1]) as f:
            conf = json.load(f)
        bn = conf.get('build_number', 0)
        conf['image'] = image[1]
        if bn is None:
            bn = 0
        img_confs[bn] = conf
    images = [img_confs[k]['image']
              for k in sorted(img_confs.keys(), reverse=True)]
    if images:
        return images[0]
    return (None, None)


def get_run_base_of_dev_image(image):
    """
    Get the run image associated to a given dev image
    """
    # FIXME: this could yield a mismatch between casa-run and casa-dev
    # versions. We should iterate on JSONs on the server until we find the run
    # image with the correct uuid)
    assert '-dev-' in image
    return image.replace('-dev-', '-run-', 1)


def iter_images(base_directory=casa_distro_directory(), **filter):
    """
    Iterate over locally installed images, with filtering.
    Filtering may be environment-driven (filter from existing environments),
    or image-driven (filter local image names even if they are not used in any
    environment). Image-driven mode is used if none of the environment
    selection filters are used (name, system, distro and branch are all None).
    If you with to trigger the environment-driven mode without filtering, just
    select "*" as one of the environment filter variables.
    """
    configs = set()  # avoid duplicates
    if filter.get('name') or filter.get('system') \
            or filter.get('image_version') or filter.get('distro') \
            or filter.get('branch') or filter.get('type'):
        # select by environment
        for config in iter_environments(base_directory,
                                        type=filter.get('type'),
                                        distro=filter.get('distro'),
                                        branch=filter.get('branch'),
                                        system=filter.get('system'),
                                        image_version=filter.get(
                                            'image_version'),
                                        name=filter.get('name'),
                                        image=filter.get('image')):
            image = (config['container_type'], config['image'])
            if filter.get('type') == 'run':
                # run / user environments may be linked to a user image
                with open('%s.json' % image[1]) as f:
                    image_meta = json.load(f)
                if image_meta['type'] != filter['type']:
                    continue
            if image[1] not in configs:
                configs.add(image[1])
                yield image

    else:
        # select all environments first (some envd may refer to images which
        # are not present locally)
        if not filter.get('image'):
            for config in iter_environments(base_directory):
                image = (config['container_type'], config['image'])
                if image[1] not in configs:
                    configs.add(image[1])
                    yield image
        # select by images
        image_filter = filter.get('image')
        for image in singularity.iter_images(base_directory=base_directory):
            if not image_filter or fnmatch.filter([image], image_filter):
                if image not in configs:
                    yield ('singularity', image)
        # for image in docker.iter_images(base_directory=base_directory):
        #     if not filter or fnmatch.filter([image], filter):
        #         yield ('docker', image)
        # for image in vbox.iter_images(base_directory=base_directory):
        #     if not filter or fnmatch.filter([image], filter):
        #         yield ('vbox', image)


def image_remote_location(container_type, image_name, url):
    if container_type == 'docker':
        raise NotImplementedError('docker case not implemented yet.')
    return '%s/%s' % (url, osp.basename(image_name))


def update_container_image(container_type, image_name, url,
                           base_directory=casa_distro_directory(),
                           force=False,
                           verbose=None, new_only=False):
    """
    Download a container image.

    Parameters
    ----------
    container_type: str
        singularity, docker, vbox
    image_name: str
        image filename (full path)
    url: str
        pattern (e.g. 'https://brainvisa.info/download/{container_type}')
    base_directory: str
        Directory where images and environments are stored.
    force: bool
        download image even if it seems up-to-date
    verbose: file
        print things
    new_only: bool
        download only if the local image is missing (don't really update)
    """
    url = url.format(container_type=container_type)
    remote_image = image_remote_location(container_type, image_name, url)

    if not os.path.isabs(image_name) and not osp.exists(image_name):
        image_name = osp.join(base_directory, image_name)

    image = osp.basename(image_name)

    metadata_file = '%s.json' % image_name
    if new_only and osp.exists(metadata_file) and osp.exists(image_name):
        print('image %s already exists.' % image, file=verbose)
        return  # don't update

    json_url = '%s.json' % remote_image

    # retreive list of compatible images
    images = url_listdir(osp.dirname(json_url))
    image_url_base, image_ext = osp.splitext(remote_image)
    image_url_pattern = re.compile(
        osp.basename(image_url_base)
        + '(-([0-9]+))?\%s\.json' % image_ext)  # noqa: W605
    images_dict = {}
    for image_url in images:
        m = image_url_pattern.match(image_url)
        if m:
            if m.group(2) is None:
                num = 0
            else:
                num = int(m.group(2))
            images_dict[num] = '%s/%s' % (osp.dirname(json_url), image_url)

    local_metadata = {}
    if osp.exists(metadata_file):
        local_metadata = json.load(open(metadata_file))
    image_version = local_metadata.get('image_version')

    for num in reversed(sorted(images_dict.keys())):
        json_url = images_dict[num]
        try:
            f = None
            try:
                f = urlopen(json_url)
                if f.getcode() == 404:
                    continue
                metadata = json.loads(f.read().decode('utf-8'))
            except Exception as e:
                print('%s could not be read:' % json_url, e)
        finally:
            if f:
                f.close()
            del f
        if image_version and metadata.get('image_version') != image_version:
            # mismatching
            continue
        # check compatibility ?
        break
    else:
        print('no matching image found for', image)
        return

    remote_image = json_url[:-5]
    # image_name = osp.join(base_directory, osp.basename(remote_image))

    if not force and osp.exists(metadata_file):
        if local_metadata.get('md5') == metadata.get('md5') \
                and local_metadata.get('size') == metadata.get('size') \
                and osp.isfile(image_name) \
                and os.stat(image_name).st_size == metadata.get('size'):
            # if verbose:
            print('image %s is up-to-date.' % image)
            return
    # if verbose:
    print('pulling image: %s' % image_name)
    tmp_metadata_file = list(osp.split(metadata_file))
    tmp_metadata_file[-1] = '.%s' % tmp_metadata_file[-1]
    tmp_metadata_file = osp.join(*tmp_metadata_file)

    tmp_image_name = list(osp.split(image_name))
    tmp_image_name[-1] = '.%s' % tmp_image_name[-1]
    tmp_image_name = osp.join(*tmp_image_name)

    download_all = True
    if osp.exists(tmp_metadata_file):
        older_metadata = json.load(open(tmp_metadata_file))
        if older_metadata['md5'] == metadata['md5']:
            download_all = False

    json.dump(metadata, open(tmp_metadata_file, 'w'),
              indent=4, separators=(',', ': '))
    downloader.download_file(remote_image, image_name,
                             allow_continue=not download_all,
                             use_tmp=True,
                             md5_check=metadata['md5'],
                             callback=downloader.stdout_progress)

    # move metadata to final location
    os.rename(tmp_metadata_file, metadata_file)


def delete_image(container_type, image_name):
    """
    Delete a container image files
    """
    if container_type in ('singularity', 'vbox'):
        if not os.path.isabs(image_name) and not osp.exists(image_name):
            image_name = osp.join(casa_distro_directory(), image_name)
        if os.path.exists(image_name):
            os.unlink(image_name)
        metadata = '%s.json' % image_name
        if os.path.exists(metadata):
            os.unlink(metadata)
    elif container_type == 'docker':
        raise NotImplementedError('docker case not implemented yet.')
    else:
        raise ValueError('unknown container type: %s' % container_type)


def select_environment(base_directory, **kwargs):
    """
    Select a single environment given its name or an existing distro directory.
    """
    env_list = list(iter_environments(base_directory, **kwargs))
    if len(env_list) == 1:
        return env_list[0]
    if len(env_list) > 1:
        raise ValueError(
            'Several environments found, use a more selective criterion: {0}'
            .format(', '.join(i['name'] for i in env_list))
        )
    raise ValueError('Cannot find any environment to perform the requested. '
                     'action. base_directory="{0}", selection={1}'
                     .format(base_directory, kwargs))


def standard_dirs_to_mount():
    """List "standard" mount points that exist on the host machine.

    Standard mount points include:
    - directories defined by the Filesystem Hierarchy Standard where users may
      store useful data;
    - directories that are used in NeuroSpin for storing data or installed
      software.
    """
    standard_dirs = (['/home', '/mnt', '/media', '/srv',
                      '/neurospin', '/i2bm', '/nfs']
                     + glob('/volatile*'))
    for dirname in standard_dirs:
        if os.path.isdir(dirname) and not os.path.islink(dirname):
            yield dirname


def prepare_user_config():
    """Write an initial configuration file with "standard" mount points.

    See standard_dirs_to_mount() for a list of "standard" mount points.
    """
    user_config_file = user_config_filename()
    if os.path.exists(user_config_file):
        return  # config file already exists, do nothing

    auto_generated_config = {
        'mounts': {dirname: dirname for dirname in standard_dirs_to_mount()},
    }
    user_config_dir = os.path.dirname(user_config_file)
    try:
        # the exist_ok parameter of os.makedirs does not exist in Python < 3.2)
        os.makedirs(user_config_dir)
    except OSError:
        pass  # probably a FileExistsError
    try:
        with open(user_config_file, 'w') as f:
            json.dump(auto_generated_config, f,
                      indent=4, sort_keys=True, separators=(',', ': '))
            f.write('\n')
    except Exception:
        print('Warning: could not create the user configuration file ({0})'
              .format(user_config_file))
        pass  # give up with a warning, this config file is not essential


def prepare_environment_homedir(casa_home_host_path):
    """Create or complete a home directory for an environment.

    This function has two roles:
    - It must create and initialize a home directory at setup time (it is
      called by setup_user or setup_dev).

    - It is also called every time a command is started in an environment,
      because the home directory may need to be created (in the per-user
      homedir scenario, i.e. shared installations). For that reason, please
      keep this function nice and short. It must also be idempotent.
    """
    if not osp.exists(casa_home_host_path):
        os.makedirs(casa_home_host_path)
    bashrc = osp.join(casa_home_host_path, '.bashrc')
    if not osp.exists(bashrc):
        with open(bashrc, 'w') as f:
            print(r'''
# .bashrc
#
# The default bashrc settings are stored in the Singularity images, so that
# they can be updated appropriately when the images evolve. Please keep this as
# the first command of this file.
. /casa/bashrc

# Users can customize their bash environment below this line. Example
# customizations are included, please uncomment them to try them out.

# If set, the pattern "**" used in a pathname expansion context will
# match all files and zero or more directories and subdirectories.
#shopt -s globstar

# colored GCC warnings and errors
#export GCC_COLORS='error=01;31:warning=01;35:note=01;36:caret=01;32:locus=01:quote=01'

# Include the current Git branch and Git status in the prompt. VERY USEFUL for
# developers, but disabled by default because it slows down the prompt on slow
# filesystems.
#source /usr/lib/git-core/git-sh-prompt
#GIT_PS1_SHOWDIRTYSTATE=true
#GIT_PS1_SHOWSTASHSTATE=true
#GIT_PS1_SHOWUNTRACKEDFILES=true
#GIT_PS1_SHOWUPSTREAM=auto
#GIT_PS1_DESCRIBE_STYLE=describe
#GIT_PS1_SHOWCOLORHINTS=true
#PS1='${CASA_ENVIRONMENT:+($CASA_ENVIRONMENT)}\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\[\033[01m\]$(__git_ps1 " (%s)")\[\033[00m\]\$ '

# Allow quickly changing the current directory to sub-directories of
# /casa/host, for example:
#     cd src/axon/master
#CDPATH=:/casa/host
''', file=f)  # noqa: E501
    # Create an (empty) .sudo_as_admin_successful file to prevent the startup
    # message telling to use "sudo" in the container, which does not work under
    # Singularity.
    silence_file = osp.join(casa_home_host_path, '.sudo_as_admin_successful')
    if not osp.exists(silence_file):
        with open(silence_file, 'w') as f:
            pass


def run_container(config, command, gui, opengl, root, cwd, env, image,
                  container_options, base_directory, verbose):
    """
    Run a command in the container defined in the environment

    Return the exit code of the command, or raise an exception if the command
    cannot be run.
    """
    if not os.path.exists(osp.join(config['directory'], 'home')):
        full_environment_path_flat = (
            osp.normcase(osp.abspath(config['directory']))
            .lstrip(os.sep)
            .replace(os.sep, '_')
        )
        xdg_data_home = os.environ.get('XDG_DATA_HOME', '')
        if not xdg_data_home:
            xdg_data_home = os.path.join(os.path.expanduser('~'),
                                         '.local', 'share')
        host_path_of_container_home = os.path.join(
            xdg_data_home, 'casa-distro',
            full_environment_path_flat, 'home')
    else:
        host_path_of_container_home = osp.join(config['directory'], 'home')

    config.setdefault('mounts', {})
    config['mounts']['/casa/home'] = host_path_of_container_home
    for dirname in standard_dirs_to_mount():
        config['mounts'].setdefault(dirname, dirname)

    # Prepare the home directory of the container (create it if needed, and
    # ensure that necessary files are present.)
    prepare_environment_homedir(host_path_of_container_home)
    prepare_user_config()

    container_type = config.get('container_type')
    if container_type == 'singularity':
        module = singularity
    elif container_type == 'vbox':
        raise NotImplementedError(
            'run command is not implemented for VirtualBox')
        module = vbox
    elif container_type == 'docker':
        raise NotImplementedError('run command is not implemented for Docker')
    else:
        raise ValueError('Invalid container type: {0}'.format(container_type))

    # check image compatibility
    if image is not None:
        eimage = image
    else:
        eimage = config.get('image')
    cid = config.get('image_id')
    if os.path.exists(eimage + '.json') and cid:
        with open(eimage + '.json') as f:
            image_meta = json.load(f)

        if cid != image_meta.get('image_id'):
            # check if image_version matches
            civ = config.get('image_version')
            if not civ and civ != image_meta.get('image_version'):
                # not the same version: check compatibility list
                compat = image_meta.get('compatibility', [])
                if cid not in compat:
                    raise ValueError('The selected image is incompatible with '
                                     'the environment to run')

    env = (env.copy() if env else {})
    branch = config.get('branch')
    if branch:
        env['CASA_BRANCH'] = bv_maker_branches.get(branch, branch)
    return module.run(config,
                      command=command,
                      gui=gui,
                      opengl=opengl,
                      root=root,
                      cwd=cwd,
                      env=env,
                      image=image,
                      container_options=container_options,
                      base_directory=base_directory,
                      verbose=verbose)


class BBIDaily:
    def __init__(self, base_directory, jenkins=None):
        # This environment variable must be set by the caller of BBIDaily, to
        # ensure that all recursively called instances of casa_distro will use
        # the correct base_directory.
        assert os.environ['CASA_BASE_DIRECTORY'] == base_directory
        self.bbe_name = 'BBE-{0}-{1}'.format(getpass.getuser(),
                                             socket.gethostname())
        self.casa_distro_src = osp.dirname(osp.dirname(
            osp.dirname(__file__)))
        casa_distro = osp.join(self.casa_distro_src, 'bin',
                               'casa_distro')
        casa_distro_admin = osp.join(self.casa_distro_src, 'bin',
                                     'casa_distro_admin')
        self.casa_distro_cmd = [sys.executable, casa_distro]
        self.casa_distro_admin_cmd = [sys.executable, casa_distro_admin]
        self.jenkins = jenkins
        if self.jenkins:
            if not self.jenkins.job_exists(self.bbe_name):
                self.jenkins.create_job(self.bbe_name)

    def log(self, environment, task_name, result, log,
            duration=None):
        if self.jenkins:
            self.jenkins.create_build(environment=environment,
                                      task=task_name,
                                      result=result,
                                      log=log+'\n',
                                      duration=duration)
        else:
            name = '{0}:{1}'.format(environment, task_name)
            print()
            print('  /-' + '-' * len(name) + '-/')
            print(' / ' + name + ' /')
            print('/-' + '-' * len(name) + '-/')
            print()
            print(log)

    def call_output(self, args, **kwargs):
        p = subprocess.Popen(args, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, bufsize=-1,
                             **kwargs)
        output, nothing = p.communicate()
        if not isinstance(output, str) and sys.version_info[0] >= 3:
            output = output.decode('UTF8')
        log = ['-'*40,
               '$ ' + ' '.join(shlex_quote(arg) for arg in args),
               '-'*40,
               output]

        return p.returncode, '\n'.join(log)

    def update_casa_distro(self):
        start = time.time()
        result, log = self.call_output(['git',
                                        '-C', self.casa_distro_src,
                                        'pull'])
        duration = int(1000 * (time.time() - start))
        self.log(self.bbe_name, 'update casa_distro',
                 result, log,
                 duration=duration)
        return result == 0

    def update_base_images(self, images):
        start = time.time()
        log = []
        result = None
        for image in images:
            result, output = self.call_output(self.casa_distro_cmd + [
                'pull_image', 'image={0}'.format(image)])
            log.append(output)
            if result:
                break
        duration = int(1000 * (time.time() - start))
        self.log(self.bbe_name,
                 'update images',
                 result, '\n'.join(log),
                 duration=duration)
        return result == 0

    def bv_maker(self, config, steps):
        environment = config['name']
        if self.jenkins:
            if not self.jenkins.job_exists(environment):
                self.jenkins.create_job(environment,
                                        **config)
        done = []
        failed = None
        for step in steps:
            start = time.time()
            result, log = self.call_output(self.casa_distro_cmd + [
                'bv_maker',
                'name={0}'.format(config['name']),
                '--',
                step,
            ])
            duration = int(1000 * (time.time() - start))
            self.log(environment, step, result, log, duration=duration)
            if result:
                failed = step
                break
            else:
                done.append(step)
        return (done, failed)

    def tests(self, test_config, dev_config):
        environment = test_config['name']
        if self.jenkins:
            if not self.jenkins.job_exists(environment):
                self.jenkins.create_job(environment,
                                        **test_config)
        # get test commands dict, and log it in the test config log (which may
        # be the dev log or the user image log)
        tests = self.get_test_commands(dev_config,
                                       log_config_name=test_config['name'])
        successful_tests = []
        failed_tests = []
        for test, commands in tests.items():
            log = []
            start = time.time()
            success = True
            for command in commands:
                if test_config['type'] in ('run', 'user'):
                    command = command.replace('/casa/host/build',
                                              '/casa/install')
                result, output = self.call_output(self.casa_distro_cmd + [
                    'run',
                    'name={0}'.format(test_config['name']),
                    'env=BRAINVISA_TEST_RUN_DATA_DIR=/casa/host/tests/test,'
                    'BRAINVISA_TEST_REF_DATA_DIR=/casa/host/tests/ref',
                    '--',
                    'sh', '-c', command
                ])
                log.append('=' * 80)
                log.append(output)
                log.append('=' * 80)
                if result:
                    success = False
                    if result in (124, 128+9):
                        log.append('TIMED OUT (exit code {0})'.format(result))
                    else:
                        log.append('FAILED with exit code {0}'
                                   .format(result))
                else:
                    log.append('SUCCESS (exit code {0})'.format(result))
            duration = int(1000 * (time.time() - start))
            self.log(environment, test, (0 if success else 1),
                     '\n'.join(log), duration=duration)
            if success:
                successful_tests.append(test)
            else:
                failed_tests.append(test)
        if failed_tests:
            self.log(environment, 'tests failed', 1,
                     'The following tests failed: {0}'.format(
                         ', '.join(failed_tests)))
        return (successful_tests, failed_tests)

    def get_test_commands(self, config, log_config_name=None):
        '''
        Given the config of a dev environment, return a dictionary
        whose keys are name of a test (i.e. 'axon', 'soma', etc.) and
        values are a list of commands to run to perform the test.
        '''
        cmd = self.casa_distro_cmd + [
            'run',
            'name={0}'.format(config['name']),
            'cwd=/casa/host/build',
            '--',
            'ctest', '--print-labels'
        ]
        # universal_newlines is the old name to request text-mode (text=True)
        o = subprocess.check_output(cmd, bufsize=-1,
                                    universal_newlines=True)
        labels = [i.strip() for i in o.split('\n')[2:] if i.strip()]
        log_lines = ['$ ' + ' '.join(shlex_quote(arg) for arg in cmd),
                     o, '\n']
        tests = {}
        for label in labels:
            cmd = self.casa_distro_cmd + [
                'run',
                'name={0}'.format(config['name']),
                'cwd=/casa/host/build',
                'env=BRAINVISA_TEST_REMOTE_COMMAND=echo',
                '--',
                'ctest', '-V', '-L',
                '^{0}$'.format(label)
            ] + config.get('ctest_options', [])
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, bufsize=-1,
                                 universal_newlines=True)
            o, stderr = p.communicate()
            log_lines += ['$ ' + ' '.join(shlex_quote(arg) for arg in cmd),
                          o, '\n']
            if p.returncode != 0:
                # We want to hide stderr unless ctest returns a nonzero exit
                # code. In the case of test filtering where no tests are
                # matched (e.g. with ctest_options=['-R', 'dummyfilter']), the
                # annoying message 'No tests were found!!!' is printed to
                # stderr by ctest, but it exits with return code 0.
                sys.stderr.write(stderr)
                raise RuntimeError('ctest failed with the above error')
            o = o.split('\n')
            # Extract the third line that follows each line containing ': Test
            # command:'
            commands = [o[i+3][o[i+3].find(':')+2:].strip()
                        for i in range(len(o))
                        if ': Test command:' in o[i]]
            timeouts = [o[i+1][o[i+1].find(':')+2:].strip()
                        for i in range(len(o))
                        if ': Test command:' in o[i]]
            timeouts = [x[x.find(':')+2:] for x in timeouts]
            if commands:  # skip empty command lists
                for i, command in enumerate(commands):
                    if float(timeouts[i]) < 9.999e+06:
                        command = 'timeout -k 10 %s %s' % (timeouts[i],
                                                           command)
                        commands[i] = command
                tests[label] = commands
        log_lines += ['Final test dictionary:',
                      json.dumps(tests, indent=4, separators=(',', ': '))]

        if log_config_name is None:
            log_config_name = config['name']
        self.log(log_config_name, 'get test commands', 0, '\n'.join(log_lines))
        return tests

    def recreate_user_env(self, user_config, dev_config):
        environment = user_config['name']
        if self.jenkins:
            if not self.jenkins.job_exists(environment):
                self.jenkins.create_job(environment,
                                        **user_config)
        start = time.time()
        if not os.path.exists(user_config['directory']):
            os.makedirs(user_config['directory'])
        result, log = self.call_output([
            'singularity', 'run',
            '--bind', user_config['directory'] + ':/casa/setup:rw',
            user_config['image'],
        ])
        if result == 0:
            # Make the reference test data available in the user environment
            # through a symlink to the dev environment
            user_test_ref_dir = os.path.join(user_config['directory'],
                                             'tests', 'ref')
            dev_test_ref_dir = os.path.join(dev_config['directory'],
                                            'tests', 'ref')
            if not os.path.exists(user_test_ref_dir):
                if not os.path.exists(os.path.dirname(user_test_ref_dir)):
                    os.makedirs(os.path.dirname(user_test_ref_dir))
                os.symlink(os.path.join('/host', dev_test_ref_dir),
                           user_test_ref_dir)

            for command in (dev_config.get('bbi_user_config', {})
                            .get('setup_commands', [])):
                subprocess.check_call(command, shell=True,
                                      cwd=user_config['directory'])
        duration = int(1000 * (time.time() - start))
        self.log(user_config['name'], 'recreate user env', result, log,
                 duration=duration)
        return result == 0

    def update_user_image(self, user_config, dev_config,
                          install_doc=True,
                          install_test=True):
        environment = user_config['name']
        if self.jenkins:
            if not self.jenkins.job_exists(environment):
                self.jenkins.create_job(environment,
                                        **user_config)
        start = time.time()
        image = user_config['image']
        if osp.exists(image):
            os.remove(image)
        result, log = self.call_output(self.casa_distro_admin_cmd + [
            'create_user_image',
            'version={0}'.format(user_config['version']),
            'name={0}'.format(user_config['name']),
            'environment_name={0}'.format(dev_config['name']),
            'output=' + image,
            'force=yes',
            'install_doc=' + str(install_doc),
            'install_test=' + str(install_test),
        ])
        duration = int(1000 * (time.time() - start))
        self.log(user_config['name'], 'update user image', result, log,
                 duration=duration)
        return result == 0
