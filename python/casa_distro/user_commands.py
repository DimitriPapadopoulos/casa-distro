from __future__ import absolute_import
from __future__ import print_function

import sys
import tempfile
import os.path as osp
import six
import time
import subprocess
import traceback

from casa_distro import linux_os_ids
from casa_distro.command import command
from casa_distro.defaults import (default_build_workflow_repository,
                                  default_repository_server,
                                  default_repository_server_directory,
                                  default_repository_login,
                                  default_distro,
                                  default_branch)
from casa_distro.build_workflow import iter_build_workflow, run_container


def display_summary(status):
    # Display summary
    sys.stdout.flush()
    sys.stderr.flush()
    messages = ['\ncasa_distro summary:']
    print(messages[0])

    global_failed = False
    first_start = None
    last_stop = None
    for (d, b, s), (es, bwf_dir) in six.iteritems(status):
        status = es.get_status_mapped()
        if status != '':
            message = '%s distro=%s branch=%s system=%s: %s' % (status, d, 
                                                                b, s, bwf_dir)
            start = es.start_time
            if start:
                if first_start is None:
                    first_start = start
                message += ', started: %04d/%02d/%02d %02d:%02d' \
                    % start[:5]
            stop = es.stop_time
            if stop:
                last_stop = stop
                message += ', stopped: %04d/%02d/%02d %02d:%02d' \
                    % stop[:5]
            messages.append(message)
            print(message)
            if es.error_code:
                global_failed = True
    
    if global_failed:
        status = 'There were errors.'
    else:
        status = 'All went good.'
    print(status)


class ExecutionStatus:
    status_map = {'not run': '',
                  'succeeded': 'OK         ',
                  'failed': 'FAILED     ',
                  'interrupted': 'INTERRUPTED'}
    
    def __init__(self,
                    error_code = None, 
                    exception = None, 
                    status = 'not run',
                    start_time = None,
                    stop_time = None):
        self.error_code = error_code
        self.exception = exception
        self.status = status
        self.start_time = start_time
        self.stop_time = stop_time
        
    def get_status_mapped(self):
        return self.status_map.get(self.status)


@command
def create(distro_source=default_distro,
           distro_name=None,
           container_type = None,
           container_image = None,
           casa_branch=default_branch,
           system=linux_os_ids[0],
           not_override='bv_maker.cfg,svn.secret',
           build_workflows_repository=default_build_workflow_repository,
           verbose=None):
    '''
    Initialize a new build workflow directory. This creates a conf
    subdirectory with build_workflow.json, bv_maker.cfg and svn.secret
    files that can be edited before compilation.

    distro_source:
        Either the name of a predefined distro (on of the directory
        located in share/distro) or a directory containing the distro
        source.
    
    distro_name:
        Name of the distro that will be created. If omited, the name
        of the distro source (or distro source directory) is used.
    
    container_type: type of container thechnology to use. It can be either 
        'singularity', 'docker' or None (the default). If it is None,
        it first try to see if Singularity is installed or try to see if
        Docker is installed.
    
    container_image: image to use for the compilation container. If no
        value is given, uses the one defined in the distro.
    
    casa_branch:
        bv_maker branch to use (latest_release, bug_fix or trunk)
    
    system:
        Name of the target system.
    
    not_override:
        a coma separated list of file name that must not be overriden 
        if they already exist.
    '''
    from casa_distro.build_workflow import create_build_workflow_directory
    not_override_lst = not_override.split(',')
    bwf_directory = osp.join(build_workflows_repository, '%(distro_name)s',
                             '%(casa_branch)s_%(system)s')
    create_build_workflow_directory(build_workflow_directory=bwf_directory,
                                    distro_source=distro_source,
                                    distro_name=distro_name,
                                    container_type=container_type,
                                    container_image=container_image,
                                    casa_branch=casa_branch,
                                    system=system,
                                    not_override=not_override_lst,
                                    verbose=verbose)


@command('list')
def list_command(distro='*', branch='*', system='*', 
         build_workflows_repository=default_build_workflow_repository,
         verbose=None):
    '''
    List (eventually selected) build workflows created by "create" command.
    '''
    
    for d, b, s, bwf_dir in iter_build_workflow(build_workflows_repository,
                                                distro=distro, branch=branch,
                                                system=system):
        print('directory:', bwf_dir)
        print(open(osp.join(bwf_dir, 'conf', 'casa_distro.json')).read())

@command
def pull(distro='*', branch='*', system='*', 
         build_workflows_repository=default_build_workflow_repository, 
         repository_server=default_repository_server, 
         repository_server_directory=default_repository_server_directory,
         login=default_repository_login, verbose=None):
    '''
    Download a build workflow (except conf directory) from sftp server
    (require lftp command to be installed).
    '''
    raise NotImplementedError('This command has not been implemented yet for casa_distro 2.0')


@command
def shell(distro='*', branch='*', system='*',
          build_workflows_repository=default_build_workflow_repository,
          gui=False, interactive=True, tmp_container=True, container_options=[],
          args_list=[], verbose=None):
    '''Start a bash shell in Docker with the given repository configuration.'''
    build_workflows = list(iter_build_workflow(build_workflows_repository, 
                                               distro=distro, 
                                               branch=branch, 
                                               system=system))
    if not build_workflows:
        print('Cannot find requested build workflow.',
              'You can list existing workflows using:\n'
              'casa_distro -r %(bwf_dir)s list_build_workflows\n'
              'Or create new one using:\n'
              'casa_distro -r %(bwf_dir)s create_build_workflow'
              % {'bwf_dir':build_workflows_repository}, 
              file=sys.stderr)
        return 1
    
    if len(build_workflows) > 1:
        print('Several build workflows found, you must explicitely select one',
              'giving values for distro, system and branch. You can list',
              'existing workflows using:\n'
              'casa_distro -r %s list' 
              % build_workflows_repository, 
              file=sys.stderr)
        return 1
    if isinstance(container_options, six.string_types):
        container_options = container_options.split(' ')        
    distro, branch, system, bwf_dir = build_workflows[0]
    bwf_directory = osp.join(build_workflows_repository, '%s' % distro,
                             '%s_%s' % (branch, system))
    command = ['/bin/bash' ] + args_list
    run_container(bwf_directory, command=command, gui=gui, 
                  interactive=interactive, tmp_container=tmp_container,
                  container_options=container_options, verbose=verbose)


@command
def run(distro=None, branch=None, system=None,
        build_workflows_repository=default_build_workflow_repository,
        gui=False, interactive=False, tmp_container=True, container_options=[],
        args_list=[], verbose=None):
    '''
    Start any command in Docker with the given repository configuration.
    example:
        casa_distro -r /home/casa run branch=bug_fix ls -als /casa
    '''
    default_distro = default_branch = default_system = False
    if distro is None:
        default_distro = True
        distro = '*'
    if branch is None:
        default_branch = True
        branch = '*'
    if system is None:
        default_system = True
        system = '*'
    
    build_workflows = list(iter_build_workflow(build_workflows_repository, 
                                               distro=distro, 
                                               branch=branch, 
                                               system=system))
    if not build_workflows:
        print('Cannot find requested build workflow.',
              'You can list existing workflows using:\n'
              'casa_distro -r %(bwf_dir)s list_build_workflows\n'
              'Or create new one using:\n'
              'casa_distro -r %(bwf_dir)s create_build_workflow'
              % {'bwf_dir':build_workflows_repository}, 
              file=sys.stderr)
        return 1
    
    if (default_distro or default_branch or default_system) \
        and len(build_workflows) > 1:
        print('Several build workflows found, you must explicitely select many',
              'giving values for distro, system and branch. You can list '
              'existing workflows using:\n'
              'casa_distro -r %(bwf_dir)s list_build_workflows\n'
              'You can run bv_maker on all existing workflows using:\n'
              'casa_distro -r %(bwf_dir)s bv_maker \'distro=*\' \'branch=*\' '
              '\'system=*\''
              % {'bwf_dir':build_workflows_repository}, 
              file=sys.stderr)
        return 1

    if isinstance(container_options, six.string_types):
        container_options = container_options.split(' ')
        
    status = {}
    global_failed = False

    for d, b, s, bwf_dir in build_workflows:
        es = ExecutionStatus(start_time = time.localtime())
        status[(d, b, s)] = (es, bwf_dir)
        try:
            command = args_list
            bwf_directory = osp.join(build_workflows_repository, '%s' % d,
                                    '%s_%s' % (b, s))
            run_container(bwf_directory, command=command, gui=gui, 
                        interactive=interactive, tmp_container=tmp_container,
                        container_options=container_options, verbose=verbose)
            es.stop_time = time.localtime()
            es.error_code = 0
            es.status = 'succeeded'
        
        except subprocess.CalledProcessError:
            global_failed = True
            es.stop_time = time.localtime()
            es.exception = traceback.format_exc()
            es.error_code = 1
            es.status = 'failed'
            
        except KeyboardInterrupt:
            global_failed = True
            es.stop_time = time.localtime()
            es.error_code = 1
            es.status = 'interrupted'
            break

    display_summary(status)
    return global_failed


@command
def bv_maker(distro=None, branch=None, system=None,
             build_workflows_repository=default_build_workflow_repository,
             gui=False, interactive=False, tmp_container=True, 
             container_options=[], args_list=[], verbose=None):
    '''
    Start bv_maker in Docker for all the selected build workflows (by default,
    all created build workflows).'''    
    args_list = ['bv_env_host', 'bv_maker' ] + args_list
    run(distro=distro, branch=branch, system=system,
         build_workflows_repository=build_workflows_repository, gui=gui,
         interactive=interactive, tmp_container=tmp_container,
         container_options=container_options, args_list=args_list,
         verbose=verbose)
