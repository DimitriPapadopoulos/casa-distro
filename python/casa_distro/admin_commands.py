# -*- coding: utf-8 -*-
from __future__ import absolute_import, print_function

import datetime
from getpass import getpass
import glob
import json
import os
import os.path as osp
from subprocess import check_call
import tempfile
import sys

from casa_distro.info import __version__ as casa_distro_version
from casa_distro.info import version_major, version_minor

from casa_distro import log, six
from casa_distro.command import command
from casa_distro.defaults import (default_build_workflow_repository,
                                  default_repository_server,
                                  default_repository_server_directory,
                                  default_repository_login)
from casa_distro.defaults import default_download_url

from casa_distro.vbox import vbox_create_system
from casa_distro.hash import file_hash

try:
    # Try Python 3 only import
    from urllib.request import urlretrieve
except ImportError:
    from urllib import urlretrieve


@command
def create_release_plan(components=None, build_workflows_repository=default_build_workflow_repository, verbose=None):
    '''create a release plan file by reading sources.'''
    from casa_distro.bv_maker import update_release_plan_file
    
    update_release_plan_file(erase_file=True,
                             components=components,
                             build_workflows_repository=build_workflows_repository,
                             verbose=verbose)


@command
def update_release_plan(components=None, build_workflows_repository=default_build_workflow_repository, verbose=None):
    '''update a release plan file by reading sources.'''
    from casa_distro.bv_maker import update_release_plan_file

    update_release_plan_file(erase_file=False,
                             components=components,
                             build_workflows_repository=build_workflows_repository,
                             verbose=verbose)


@command
def html_release_plan(login=None, password=None, build_workflows_repository=default_build_workflow_repository, verbose=None):
    '''Convert a release plan to an HTML file for human inspection'''
    from casa_distro.bv_maker import release_plan_to_html
    release_plan_file = osp.join(build_workflows_repository, 'release_plan.yaml')
    release_plan_html = osp.join(build_workflows_repository, 'release_plan.html')
    release_plan_to_html(release_plan_file, release_plan_html)


@command
def create_latest_release(build_workflows_repository=default_build_workflow_repository, dry=None, ignore_warning = False, verbose=True):
    '''apply actions defined in the release plan file for the creation of the latest_release branch.'''
    import os, types
    from distutils.util import strtobool
    from casa_distro.bv_maker import apply_latest_release_todo
    
    try:
        if isinstance(dry, (bytes, str)):
            dry = bool(strtobool(dry))

        else:
            dry = bool(dry)
    except:
        print('dry argument must contain a value convertible to boolean', 
              file = sys.stderr)
        sys.exit(1)

    try:
        if isinstance(ignore_warning, (bytes, str)):
            ignore_warning = bool(strtobool(ignore_warning))

        else:
            ignore_warning = bool(ignore_warning)
    except:
        print('ignore_warning argument must contain a value convertible to',
              'boolean', file = sys.stderr)
        sys.exit(1)
    
    
    release_plan_file = osp.join(build_workflows_repository, 
                                 'release_plan.yaml')
    previous_run_output = osp.join(build_workflows_repository, 
                                   'create_latest_release.log')
        
    try:
        fail_on_error = True
        fail_on_warning = not ignore_warning
        
        apply_latest_release_todo(release_plan_file, previous_run_output, dry, fail_on_warning, fail_on_error, verbose)
        
    except RuntimeError as e:
        print('Impossible to apply release plan.', e.message,
              file = sys.stderr)
        raise
        
@command
def create_docker(image_names = '*', verbose=None, no_host_network=False):
    '''create or update all casa-test and casa-dev docker images

      image_names:
          filter for images which should be rebuilt. May be a coma-separated list, wildcards are allowed. Default: *

          Image names have generally the shape "cati/<type>:<system>". Image
          types and systems may be one of the buitin ones found in
          casa-distro (casa-test, casa-dev, cati_platform), or one user-defined
          which will be looked for in $HOME/.config/casa-distro/docker,
          $HOME/.casa-distro/docker, or in the share/docker subdirectory inside
          the main repository directory.
    '''
    from casa_distro.docker import create_docker_images
    
    image_name_filters = image_names.split(',')
    count = create_docker_images(
        image_name_filters = image_name_filters,
        no_host_network=bool(no_host_network))
    if count == 0:
        print('No image match filter "%s"' % image_names, file=sys.stderr)
        return 1

@command
def update_docker(image_names = '*', verbose=None):
    '''pull all casa-test and casa-dev docker images from DockerHub'''
    from casa_distro.docker import update_docker_images
    
    image_name_filters = image_names.split(',')
    count = update_docker_images(
        image_name_filters = image_name_filters)
    if count == 0:
        print('No image match filter "%s"' % image_names, file=sys.stderr)
        return 1


@command
def publish_docker(image_names = '*', verbose=None):
    '''publish docker images on dockerhub.com for public images or sandbox.brainvisa.info for private images'''
    from casa_distro.docker import publish_docker_images
    image_name_filters = image_names.split(',')
    count = publish_docker_images(
        image_name_filters = image_name_filters)
    if count == 0:
        print('No image match filter "%s"' % image_names, file=sys.stderr)
        return 1

@command
def create_singularity(image_names = 'cati/*',
                       build_workflows_repository=default_build_workflow_repository,
                       verbose=None):
    '''create or update all casa-test and casa-dev docker images'''
    from casa_distro.singularity import create_singularity_images
    
    image_name_filters = image_names.split(',')
    count = create_singularity_images(
        bwf_dir=build_workflows_repository,
        image_name_filters = image_name_filters,
        verbose=verbose)
    if count == 0:
        print('No image match filter "%s"' % image_names, file=sys.stderr)
        return 1

@command
def publish_singularity(image_names = 'cati/*',
                        build_workflows_repository=default_build_workflow_repository,
                        repository_server=default_repository_server, 
                        repository_server_directory=default_repository_server_directory,
                        login=default_repository_login, verbose=None):
    '''Publish singularity images to the sftp server'''
    verbose = log.getLogFile(verbose)
    
    image_name_filters = [i.replace('/', '_').replace(':', '_') for i in image_names.split(',')]
    image_files = []
    for filter in image_name_filters:
        image_files += glob.glob(osp.join(build_workflows_repository, filter + '.simg'))
    if not image_files:
        print('No image match filter "%s"' % image_names, file=sys.stderr)
        return 1

    # check if uploads are needed or if images already habe valid md5 on the
    # server
    server_url = default_download_url
    valid_files = []
    for image_file in image_files:
        hash_path = image_file + '.md5'
        hash_file = os.path.basename(hash_path)
        with open(hash_path) as f:
            local_hash = f.read()
        tmp = tempfile.NamedTemporaryFile()
        url = '%s/%s' % (server_url, hash_file)
        try:
            if verbose:
                print('check image:', url)
            urlretrieve(url, tmp.name)
            with open(tmp.name) as f:
                remote_hash = f.read()
            if remote_hash == local_hash:
                valid_files.append(image_file)
                if verbose:
                    print('Not updating', image_file, 'which is up-to-date',
                          file=verbose)
        except Exception as e:
            pass # not on server
    image_files = [f2 for f2 in image_files if f2 not in valid_files]
    if len(image_files) == 0:
        # nothing to do
        if verbose:
            print('All images are up-to-date.')
        return

    lftp_script = tempfile.NamedTemporaryFile()
    if login:
        remote = 'sftp://%s@%s' % (login, repository_server)
    else:
        remote = 'sftp://%s' % repository_server
    print('connect', remote, file=lftp_script)
    print('cd', repository_server_directory, file=lftp_script)
    for f in image_files:
        print('put', f, file=lftp_script)
        print('put', f + '.md5', file=lftp_script)
        if os.path.exists(f + '.dockerid'):
            print('put', f + '.dockerid', file=lftp_script)
    lftp_script.flush()
    cmd = ['lftp', '-f', lftp_script.name]
    if verbose:
        print('Running', *cmd, file=verbose)
        print('-' * 10, lftp_script.name, '-'*10, file=verbose)
        with open(lftp_script.name) as f:
            print(f.read(), file=verbose)
        print('-'*40, file=verbose)
    check_call(cmd)

@command
def publish_build_workflows(distro='*', branch='*', system='*', 
                            build_workflows_repository=default_build_workflow_repository, 
                            repository_server=default_repository_server, 
                            repository_server_directory=default_repository_server_directory,
                            login=default_repository_login, verbose=None):
    '''Upload a build workflow to sftp server (require lftp command to be installed).'''
    
    from casa_distro import iter_build_workflow
    
    verbose = log.getLogFile(verbose)
    lftp_script = tempfile.NamedTemporaryFile()
    if login:
        remote = 'sftp://%s@%s' % (login, repository_server)
    else:
        remote = 'sftp://%s' % repository_server
    print('connect', remote, file=lftp_script)
    print('cd', repository_server_directory, file=lftp_script)
    for d, b, s, bwf_dir in iter_build_workflow(build_workflows_repository, distro=distro, branch=branch, system=system):
        relative_bwf_dir = bwf_dir[len(build_workflows_repository)+1:]
        
        cmd = ['mirror', '-R', '--delete', bwf_dir, relative_bwf_dir]
        if verbose:
            cmd.insert(2, '-v')
        print(*cmd, file=lftp_script)
    lftp_script.flush()
    cmd = ['lftp', '-f', lftp_script.name]
    if verbose:
        print('Running', *cmd, file=verbose)
        print('-' * 10, lftp_script.name, '-'*10, file=verbose)
        with open(lftp_script.name) as f:
            print(f.read(), file=verbose)
        print('-'*40, file=verbose)
    check_call(cmd)


@command
def create_system(iso='~/Downloads/ubuntu-*.iso', image_name='casa-{iso}',
                  output='~/casa_distro/{image_name}.vdi',
                  container_type='vbox'):
    '''First step for the creation of base system VirtualBox image'''
    
    if container_type != 'vbox':
        raise ValueError('Only "vbox" container type requires to create a system image')
    
    if not osp.exists(iso):
        isos = glob.glob(osp.expandvars(osp.expanduser(iso)))
        if len(isos) == 0:
            # Raise appropriate error for non existing file
            open(iso)
        elif len(isos) > 1:
            raise ValueError('Several iso files found : {0}'.format(', '.join(isos)))
        iso = isos[0]

    image_name = image_name.format(iso=osp.splitext(osp.basename(iso))[0])
    output = osp.expandvars(osp.expanduser(output)).format(image_name=image_name)


    metadata_output = output + '.json'
    print('Create metadata in', metadata_output)
    metadata = {
        'image_name': image_name,
        'container_type': 'vbox',
        'creation_time': datetime.datetime.now().isoformat(),
        'iso': osp.basename(iso),
        'iso_time': datetime.datetime.fromtimestamp(os.stat(iso).st_mtime).isoformat(),
    }
    json.dump(metadata, open(metadata_output, 'w'), indent=4)
    
    vbox_create_system(image_name=image_name, 
                       iso=iso,
                       output=output,
                       verbose=sys.stdout)
    
    print('''4) Perform Ubuntu minimal installation with an autologin account named "brainvisa" and with password "brainvisa"
5) Perform system updates and install kernel module creation packages :

.. code::

    sudo apt update
    sudo apt upgrade
    sudo apt install gcc make perl

6) Set root password to "brainvisa" (this is necessary to automatically connect to the VM to perform post-install)
7) Reboot the VM
8) Download and install VirtualBox guest additions
9) Shut down the VM
10) Configure the VM in VirualBox (especially 3D acceleration, processors and memory)
''')
    


def publish_system(system='~/casa_distro/casa-ubuntu-*.vdi',
                   container_type='vbox'):
    '''Upload a system image on brainvisa.info web site'''
    
    if container_type != 'vbox':
        raise ValueError('Only "vbox" container type requires to create a system image')
    
    if not osp.exists(system):
        systems = glob.glob(osp.expandvars(osp.expanduser(system)))
        if len(systems) == 0:
            # Raise appropriate error for non existing file
            open(system)
        elif len(systems) > 1:
            raise ValueError('Several system files found : {0}'.format(', '.join(systems)))
        system = systems[0]
    
    # Add system file md5 hash to JSON metadata file
    metadata_file = system + '.json'
    metadata = json.load(open(metadata_file))
    metadata['md5'] = file_hash(system)
    json.dump(metadata, open(metadata_file, 'w'), indent=4)
    
    raise NotImplementedError()


@command
def vbox_create_run(system='~/casa_distro/casa-ubuntu-*.vdi',
                    output='~/casa_distro/casa-run.vdi'):
    '''Creation of a run VirtualBox image'''
    
    if not osp.exists(system):
        systems = glob.glob(osp.expandvars(osp.expanduser(system)))
        if len(systems) == 0:
            # Raise appropriate error for non existing file
            open(system)
        elif len(systems) > 1:
            raise ValueError('Several system files found : {0}'.format(', '.join(systems)))
        system = systems[0]
    output = osp.expandvars(osp.expanduser(output))
    
    image_name = osp.splitext(osp.basename(output))[0]
    print('Create Linux 64 bits virtual machine')
    check_call(['VBoxManage', 'createvm', 
                '--name', image_name, 
                '--ostype', 'Ubuntu_64',
                '--register'])
    print('Set memory to 8 GiB and allow booting on DVD')
    check_call(['VBoxManage', 'modifyvm', image_name,
                '--memory', '8192',
                '--vram', '64',
                '--boot1', 'dvd',
                '--nic1', 'nat'])
    print('Create a 128 GiB system disk in', output)
    check_call(['VBoxManage', 'createmedium',
                '--filename', output,
                '--size', '131072',
                '--format', 'VDI',
                '--variant', 'Standard'])
    print('Copy system disk to', output)
    check_call(['VBoxManage', 'clonemedium', 'disk',
                system, output, '--existing'])
    print('Create a SATA controller in the VM')
    check_call(['VBoxManage', 'storagectl', image_name,
                '--name', '%s_SATA' % image_name,
                '--add', 'sata'])
    print('Attach the system disk to the machine')
    check_call(['VBoxManage', 'storageattach', image_name,
                '--storagectl', '%s_SATA' % image_name,
                '--medium', output,
                '--port', '1',
                '--type', 'hdd'])
    print('Start the new virtual machine')
    check_call(['VBoxManage', 'startvm', image_name])
    
