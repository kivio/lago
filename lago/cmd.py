#!/usr/bin/python
#
# Copyright 2014 Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
#
# Refer to the README and COPYING files for full details of the license
#

import argparse
import functools
import grp
import logging
import os
import pkg_resources
import shutil
import sys

import lago
import lago.config
import lago.plugins
import lago.plugins.cli
import lago.templates
from lago import log_utils, prefix as lago_prefix

CLI_PREFIX = 'lagocli-'
LOGGER = logging.getLogger('cli')


@lago.plugins.cli.cli_plugin(
    help='Initialize a directory for framework deployment'
)
@lago.plugins.cli.cli_plugin_add_argument(
    'virt_config',
    help='Configuration of resources to deploy',
    metavar='VIRT_CONFIG',
    type=os.path.abspath,
)
@lago.plugins.cli.cli_plugin_add_argument(
    'prefix',
    help=(
        'Prefix directory of the deployment, if none passed, it will use '
        '$PWD/.lago'
    ),
    metavar='PREFIX',
    type=os.path.abspath,
    nargs='?',
    default=os.path.join(os.path.curdir, '.lago'),
)
@lago.plugins.cli.cli_plugin_add_argument(
    '--template-repo-path',
    help='Repo file describing the templates',
    default='http://templates.ovirt.org/repo/repo.metadata',
)
@lago.plugins.cli.cli_plugin_add_argument(
    '--template-repo-name',
    help='Name of the repo from the template repos dir',
)
@lago.plugins.cli.cli_plugin_add_argument(
    '--template-store',
    help='Location to store templates at',
    type=os.path.abspath,
)
@log_utils.log_task('Initialize and populate prefix', LOGGER)
def do_init(
    prefix,
    virt_config,
    template_repo_path=None,
    template_repo_name=None,
    template_store=None,
    **kwargs
):
    prefix = lago_prefix.Prefix(prefix)
    prefix.initialize()
    log_utils.setup_prefix_logging(prefix.paths.logs())

    try:
        if template_repo_path:
            repo = lago.templates.TemplateRepository.from_url(
                template_repo_path
            )
        else:
            try:
                repo_name = (
                    template_repo_name
                    or lago.config.get('template_default_repo')
                )
            except KeyError:
                raise RuntimeError(
                    'No template repo was configured or specified'
                )

            repo = lago.templates.find_repo_by_name(repo_name)

        template_store_path = (
            template_store or lago.config.get(
                'template_store',
                default=None
            )
        )
        store = lago.templates.TemplateStore(template_store_path)

        with open(virt_config, 'r') as virt_fd:
            prefix.virt_conf_from_stream(virt_fd, repo, store)
    except:
        shutil.rmtree(prefix.paths.prefixed(''))
        raise


def in_prefix(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        prefix_path = lago_prefix.resolve_prefix_path()
        return func(*args, prefix=lago_prefix.Prefix(prefix_path), **kwargs)

    return wrapper


def with_logging(func):
    @functools.wraps(func)
    def wrapper(prefix, *args, **kwargs):
        log_utils.setup_prefix_logging(prefix.paths.logs())
        return func(*args, prefix=prefix, **kwargs)

    return wrapper


@lago.plugins.cli.cli_plugin(help='Clean up deployed resources')
@in_prefix
@with_logging
def do_cleanup(prefix, **kwargs):
    prefix.cleanup()


@lago.plugins.cli.cli_plugin(
    help='Cleanup and remove the whole prefix and any files in it'
)
@lago.plugins.cli.cli_plugin_add_argument(
    '-y',
    '--yes',
    help="Don't ask for confirmation, assume yes",
    action='store_true',
)
@in_prefix
@with_logging
def do_destroy(prefix, yes, **kwargs):
    prefix_path = prefix.paths.prefix
    if not yes:
        response = raw_input(
            'Do you really want to destroy %s? [Yn] ' % prefix_path
        )
        if response and response[0] not in 'Yy':
            LOGGER.info('Aborting on user input')
            return

    prefix.cleanup()
    if os.path.islink(prefix_path):
        os.unlink(prefix_path)
    else:
        shutil.rmtree(prefix_path)


@lago.plugins.cli.cli_plugin(help='Deploy lago resources')
@lago.plugins.cli.cli_plugin_add_argument(
    'vm_names',
    help='Name of the vm to start',
    metavar='VM_NAME',
    nargs='*',
)
@in_prefix
@with_logging
def do_start(prefix, vm_names=None, **kwargs):
    prefix.start(vm_names=vm_names)


@lago.plugins.cli.cli_plugin(help='Destroy lago resources')
@lago.plugins.cli.cli_plugin_add_argument(
    'vm_names',
    help='Name of the vm to stop',
    metavar='VM_NAME',
    nargs='*',
)
@in_prefix
@with_logging
def do_stop(prefix, vm_names, **kwargs):
    prefix.stop(vm_names=vm_names)


@lago.plugins.cli.cli_plugin(
    help='Create snapshots for all deployed resources'
)
@lago.plugins.cli.cli_plugin_add_argument(
    '--list',
    '-l',
    dest='list_only',
    help='List current available snapshots',
    action='store_true',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'snapshot_name',
    help='Name of the snapshot to create',
    metavar='SNAPSHOT_NAME',
    nargs='?',
    default=None,
)
@in_prefix
@with_logging
def do_snapshot(prefix, list_only, snapshot_name, out_format, **kwargs):
    if list_only:
        snapshots = prefix.get_snapshots()
        print out_format.format(snapshots)
    elif snapshot_name:
        prefix.create_snapshots(snapshot_name)
    else:
        raise RuntimeError('No snapshot name provided')


@lago.plugins.cli.cli_plugin(help='Revert resources to a snapshot')
@lago.plugins.cli.cli_plugin_add_argument(
    'snapshot_name',
    help='Name of the snapshot to revert to',
    metavar='SNAPSHOT_NAME',
)
@in_prefix
@with_logging
def do_revert(prefix, snapshot_name, **kwargs):
    prefix.revert_snapshots(snapshot_name)


@lago.plugins.cli.cli_plugin(
    help='Open shell on the domain or run as script/command',
    prefix_chars='\x00',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'args',
    help=(
        'If none provided, an interactive shell will be started.\n'
        'If arguments start with -c, what follows will be '
        'executes as a command.\n'
        'Otherwise, if a single provided, it will be ran as script'
        ' on the domain.'
    ),
    nargs='*',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'host',
    help='Host to connect to',
    metavar='HOST',
)
@in_prefix
@with_logging
def do_shell(prefix, host, args=None, **kwargs):
    args = args or []
    try:
        host = prefix.virt_env.get_vm(host)
    except KeyError:
        LOGGER.error('Unable to find VM %s', host)
        LOGGER.info(
            'Available VMs:\n\t' + '\n\t'.join(
                prefix.virt_env.get_vms().keys(
                )
            )
        )
        raise

    if not host.alive():
        raise RuntimeError('Host %s is not running' % host.name())

    host.wait_for_ssh()

    if len(args) == 0:
        result = host.interactive_ssh(['bash'])
    elif len(args) == 1 and os.path.isfile(args[0]):
        result = host.ssh_script(args[0])
    else:
        if args[0] == '-c':
            args = args[1:]

        result = host.interactive_ssh(args)

    sys.exit(result.code)


@lago.plugins.cli.cli_plugin(help='Open serial console to the domain', )
@lago.plugins.cli.cli_plugin_add_argument(
    'host',
    help='Host to connect to',
    metavar='HOST',
)
@in_prefix
@with_logging
def do_console(prefix, host, **kwargs):
    try:
        host = prefix.virt_env.get_vm(host)
    except KeyError:
        LOGGER.error('Unable to find VM %s', host)
        LOGGER.info(
            'Available VMs:\n\t' + '\n\t'.join(
                prefix.virt_env.get_vms().keys(
                )
            )
        )
        raise

    result = host.interactive_console()
    sys.exit(result.code)


@lago.plugins.cli.cli_plugin(
    help='Show status of the deployed virtual resources'
)
@in_prefix
@with_logging
def do_status(prefix, out_format, **kwargs):

    with open(prefix.paths.uuid()) as f:
        uuid = f.read()

    info_dict = {
        'Prefix': {
            'Base directory': prefix.paths.prefix,
            'UUID': uuid,
            'Networks': dict(
                (
                    net.name(),
                    {
                        'gateway': net.gw(),
                        'status': net.alive() and 'up' or 'down',
                        'management': net.is_management(),
                    }
                ) for net in prefix.virt_env.get_nets().values()
            ),
            'VMs': dict(
                (
                    vm.name(),
                    {
                        'distro': vm.distro(),
                        'root password': vm.root_password(),
                        'status': vm.alive() and 'up' or 'down',
                        'snapshots': ', '.join(vm._spec['snapshots'].keys()),
                        'VNC port': vm.vnc_port() if vm.alive() else None,
                        'metadata': vm.metadata,
                        'NICs': dict(
                            (
                                'eth%d' % i,
                                {
                                    'network': nic['net'],
                                    'ip': nic.get('ip', 'N/A'),
                                }

                            ) for i, nic in enumerate(vm.nics())
                        ),
                    }

                ) for vm in prefix.virt_env.get_vms().values()
            ),
        },
    }

    print out_format.format(info_dict)


@lago.plugins.cli.cli_plugin(
    help='Copy file from a virtual machine to local machine'
)
@lago.plugins.cli.cli_plugin_add_argument(
    'local_path',
    help='Path on the local host to copy the file/dir to',
    metavar='LOCAL_PATH',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'remote_path',
    help='Path of the file/dir to copy from the host',
    metavar='REMOTE_PATH',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'host',
    help='Host to copy files from',
    metavar='HOST',
)
@in_prefix
@with_logging
def do_copy_from_vm(prefix, host, remote_path, local_path, **kwargs):
    try:
        host = prefix.virt_env.get_vm(host)
    except KeyError:
        LOGGER.error('Unable to find VM %s', host)
        LOGGER.info(
            'Available VMs:\n\t' + '\n\t'.join(
                prefix.virt_env.get_vms().keys(
                )
            )
        )
        raise

    if not host.alive():
        raise RuntimeError('Host %s is not running' % host.name())

    host.wait_for_ssh()
    host.copy_from(remote_path, local_path)


@lago.plugins.cli.cli_plugin(
    help='Copy file/dir to a virtual machine from the local host'
)
@lago.plugins.cli.cli_plugin_add_argument(
    'remote_path',
    help='Local path to copy the file/dir to',
    metavar='REMOTE_PATH',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'local_path',
    help='Path of the file/dir to copy from the host',
    metavar='LOCAL_PATH',
)
@lago.plugins.cli.cli_plugin_add_argument(
    'host',
    help='Host to copy files to',
    metavar='HOST',
)
@in_prefix
@with_logging
def do_copy_to_vm(prefix, host, remote_path, local_path, **kwargs):
    try:
        host = prefix.virt_env.get_vm(host)
    except KeyError:
        LOGGER.error('Unable to find VM %s', host)
        LOGGER.info(
            'Available VMs:\n\t' + '\n\t'.join(
                prefix.virt_env.get_vms().keys(
                )
            )
        )
        raise

    if not host.alive():
        raise RuntimeError('Host %s is not running' % host.name())

    host.wait_for_ssh()
    host.copy_to(local_path, remote_path)


def create_parser(cli_plugins, out_plugins):
    parser = argparse.ArgumentParser(
        description='Command line interface to oVirt testing framework.'
    )
    parser.add_argument(
        '-l',
        '--loglevel',
        default='info',
        choices=['info', 'debug', 'error', 'warning'],
        help='Log level to use, by default %(default)s'
    )
    parser.add_argument(
        '--logdepth',
        default=3,
        type=int,
        help='How many task levels to show, by default %(default)s'
    )
    pkg_info = pkg_resources.require("lago")[0]
    parser.add_argument(
        '--version',
        action='version',
        version='%(prog)s ' + pkg_info.version,
    )
    parser.add_argument(
        '--out-format',
        '-f',
        action='store',
        default='default',
        choices=out_plugins.keys(),
    )
    verbs_parser = parser.add_subparsers(dest='verb', metavar='VERB')
    for cli_plugin_name, cli_plugin in cli_plugins.items():
        plugin_parser = verbs_parser.add_parser(
            cli_plugin_name, **cli_plugin.init_args
        )
        cli_plugin.populate_parser(plugin_parser)

    return parser


def check_group_membership():
    if 'lago' not in [grp.getgrgid(gid).gr_name for gid in os.getgroups()]:
        LOGGER.warning('current session does not belong to lago group.')


def main():
    cli_plugins = lago.plugins.load_plugins(
        lago.plugins.PLUGIN_ENTRY_POINTS['cli']
    )
    out_plugins = lago.plugins.load_plugins(
        lago.plugins.PLUGIN_ENTRY_POINTS['out']
    )
    parser = create_parser(cli_plugins=cli_plugins, out_plugins=out_plugins)
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG)
    logging.root.handlers = [
        log_utils.TaskHandler(
            task_tree_depth=args.logdepth,
            level=getattr(logging, args.loglevel.upper()),
            dump_level=logging.ERROR,
            formatter=log_utils.ColorFormatter(
                fmt='%(msg)s',
            )
        )
    ]

    check_group_membership()

    args.out_format = out_plugins[args.out_format]

    try:
        cli_plugins[args.verb].do_run(args)

    except Exception:
        LOGGER.exception('Error occured, aborting')
        sys.exit(1)


if __name__ == '__main__':
    main()
