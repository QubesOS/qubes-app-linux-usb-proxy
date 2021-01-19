#!/usr/bin/python2 -O
# vim: fileencoding=utf-8
#
# The Qubes OS Project, https://www.qubes-os.org/
#
# Copyright (C) 2016  Marek Marczykowski-Górecki
#                                   <marmarek@invisiblethingslab.com>
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
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
import asyncio
import fcntl
import grp
import os
import re
import string
import subprocess

import errno
import tempfile

import qubes.devices
import qubes.ext
import qubes.vm.adminvm

usb_device_re = re.compile(r"^[0-9]+-[0-9]+(_[0-9]+)*$")
# should match valid VM name
usb_connected_to_re = re.compile(br"^[a-zA-Z][a-zA-Z0-9_.-]*$")
usb_device_hw_ident_re = re.compile(r'^[0-9a-f]{4}:[0-9a-f]{4} ')

class USBDevice(qubes.devices.DeviceInfo):
    # pylint: disable=too-few-public-methods
    def __init__(self, backend_domain, ident):
        super(USBDevice, self).__init__(backend_domain, ident, None)

        self._qdb_ident = ident.replace('.', '_')
        self._qdb_path = '/qubes-usb-devices/' + self._qdb_ident
        # lazy loading
        self._description = None


    @property
    def description(self):
        if self._description is None:
            if not self.backend_domain.is_running():
                # don't cache this value
                return "Unknown - domain not running"
            untrusted_device_desc = self.backend_domain.untrusted_qdb.read(
                self._qdb_path + '/desc')
            if not untrusted_device_desc:
                return 'Unknown'
            self._description = self._sanitize_desc(untrusted_device_desc)
            hw_ident_match = usb_device_hw_ident_re.match(self._description)
            if hw_ident_match:
                self._description = self._description[
                    len(hw_ident_match.group(0)):]
        return self._description

    @property
    def frontend_domain(self):
        if not self.backend_domain.is_running():
            return None
        untrusted_connected_to = self.backend_domain.untrusted_qdb.read(
            self._qdb_path + '/connected-to'
        )
        if not untrusted_connected_to:
            return None
        if not usb_connected_to_re.match(untrusted_connected_to):
            self.backend_domain.log.warning(
                'Device {} has invalid chars in connected-to '
                'property'.format(self.ident))
            return None
        untrusted_connected_to = untrusted_connected_to.decode(
            'ascii', errors='strict')
        try:
            connected_to = self.backend_domain.app.domains[
                untrusted_connected_to]
        except KeyError:
            self.backend_domain.log.warning(
                'Device {} has invalid VM name in connected-to '
                'property: '.format(self.ident, untrusted_connected_to))
            return None
        return connected_to

    @staticmethod
    def _sanitize_desc(untrusted_device_desc):
        untrusted_device_desc = untrusted_device_desc.decode('ascii',
            errors='ignore')
        safe_set = set(string.ascii_letters + string.digits +
                       string.punctuation + ' ')
        return ''.join(
            c if c in safe_set else '_' for c in untrusted_device_desc
        )


class USBProxyNotInstalled(qubes.exc.QubesException):
    pass


class QubesUSBException(qubes.exc.QubesException):
    pass


def modify_qrexec_policy(service, line, add):
    """
    Add/remove *line* to qrexec policy of a *service*.
    If policy file is missing, it is created. If resulting policy would be
    empty, it is removed.

    :param service: service name
    :param line: line to add/remove
    :param add: True if line should be added, otherwise False
    :return: None
    """
    path = '/etc/qubes-rpc/policy/{}'.format(service)
    while True:
        with open(path, 'a+') as policy:
            # take the lock here, it's released by closing the file
            fcntl.lockf(policy.fileno(), fcntl.LOCK_EX)
            # While we were waiting for lock, someone could have unlink()ed
            # (or rename()d) our file out of the filesystem. We have to
            # ensure we got lock on something linked to filesystem.
            # If not, try again.
            if os.fstat(policy.fileno()) != os.stat(path):
                continue

            policy.seek(0)

            policy_rules = policy.readlines()
            if add:
                policy_rules.insert(0, line)
            else:
                # handle also cases where previous cleanup failed or
                # was done manually
                while line in policy_rules:
                    policy_rules.remove(line)

            if policy_rules:
                with tempfile.NamedTemporaryFile(
                        prefix=path, delete=False) as policy_new:
                    policy_new.write(''.join(policy_rules).encode())
                    policy_new.flush()
                    try:
                        os.chown(policy_new.name, -1,
                            grp.getgrnam('qubes').gr_gid)
                        os.chmod(policy_new.name, 0o660)
                    except KeyError:  # group 'qubes' not found
                        # don't change mode if no 'qubes' group in the system
                        pass
                os.rename(policy_new.name, path)
            else:
                os.remove(path)
        break


class USBDeviceExtension(qubes.ext.Extension):

    def __init__(self):
        super(USBDeviceExtension, self).__init__()
        #include dom0 devices in listing only when usb-proxy is really
        # installed there
        self.usb_proxy_installed_in_dom0 = os.path.exists(
            '/etc/qubes-rpc/qubes.USB')
        self.devices_cache = {}

    @qubes.ext.handler('domain-init', 'domain-load')
    def on_domain_init_load(self, vm, event):
        '''Initialize watching for changes'''
        # pylint: disable=unused-argument,no-self-use
        vm.watch_qdb_path('/qubes-usb-devices')
        if event == 'domain-load':
            # avoid building a cache on domain-init, as it isn't fully set yet,
            # and definitely isn't running yet
            current_devices = dict((dev.ident, dev.frontend_domain)
                for dev in self.on_device_list_usb(vm, None))
            self.devices_cache[vm.name] = current_devices
        else:
            self.devices_cache[vm.name] = {}

    @asyncio.coroutine
    def _attach_and_notify(self, vm, device, options):
        # bypass DeviceCollection logic preventing double attach
        yield from self.on_device_attach_usb(vm,
            'device-pre-attach:usb', device, options)
        yield from vm.fire_event_async('device-attach:usb',
                device=device,
                options=options)

    @qubes.ext.handler('domain-qdb-change:/qubes-usb-devices')
    def on_qdb_change(self, vm, event, path):
        '''A change in QubesDB means a change in device list'''
        # pylint: disable=unused-argument,no-self-use
        vm.fire_event('device-list-change:usb')
        current_devices = dict((dev.ident, dev.frontend_domain)
            for dev in self.on_device_list_usb(vm, None))

        # compare cached devices and current devices, collect:
        # - newly appeared devices
        # - devices disconnected from a vm
        # - devices connected to a vm
        new_devices = set()
        connected_devices = dict()
        disconnected_devices = dict()
        devices_cache = self.devices_cache[vm.name]
        for dev, connected_to in current_devices.items():
            if dev not in devices_cache:
                new_devices.add(dev)
            elif devices_cache[dev] != current_devices[dev]:
                if devices_cache[dev] is not None:
                    disconnected_devices[dev] = devices_cache[dev]
                if current_devices[dev] is not None:
                    connected_devices[dev] = current_devices[dev]

        self.devices_cache[vm.name] = current_devices
        # send events about devices detached/attached outside by themselves
        # (like device pulled out or manual qubes.USB qrexec call)
        for dev_ident, front_vm in disconnected_devices.items():
            dev = USBDevice(vm, dev_ident)
            asyncio.ensure_future(front_vm.fire_event_async('device-detach:usb',
                                                            device=dev))
        for dev_ident, front_vm in connected_devices.items():
            dev = USBDevice(vm, dev_ident)
            asyncio.ensure_future(front_vm.fire_event_async('device-attach:usb',
                                                            device=dev,
                                                            options={}))
        for front_vm in vm.app.domains:
            if not front_vm.is_running():
                continue
            for assignment in front_vm.devices['usb'].assignments(
                    persistent=True):
                if assignment.backend_domain == vm and \
                        assignment.ident in new_devices:
                    asyncio.ensure_future(self._attach_and_notify(
                        front_vm, assignment.device, assignment.options))

    @qubes.ext.handler('device-list:usb')
    def on_device_list_usb(self, vm, event):
        # pylint: disable=unused-argument,no-self-use

        if not vm.is_running() or not hasattr(vm, 'untrusted_qdb'):
            return

        if isinstance(vm, qubes.vm.adminvm.AdminVM) and not \
                self.usb_proxy_installed_in_dom0:
            return

        untrusted_dev_list = vm.untrusted_qdb.list('/qubes-usb-devices/')
        if not untrusted_dev_list:
            return
        # just get list of devices, not its every property
        untrusted_dev_list = \
            set(path.split('/')[2] for path in untrusted_dev_list)
        for untrusted_qdb_ident in untrusted_dev_list:
            if not usb_device_re.match(untrusted_qdb_ident):
                vm.log.warning('Invalid USB device name detected')
                continue
            ident = untrusted_qdb_ident.replace('_', '.')
            yield USBDevice(vm, ident)

    @qubes.ext.handler('device-get:usb')
    def on_device_get_usb(self, vm, event, ident):
        # pylint: disable=unused-argument,no-self-use
        if not vm.is_running():
            return
        if vm.untrusted_qdb.list(
                        '/qubes-usb-devices/' + ident.replace('.', '_')):
            yield USBDevice(vm, ident)

    @staticmethod
    def get_all_devices(app):
        for vm in app.domains:
            if not vm.is_running():
                continue

            for dev in vm.devices['usb']:
                # there may be more than one USB-passthrough implementation
                if isinstance(dev, USBDevice):
                    yield dev

    @qubes.ext.handler('device-list-attached:usb')
    def on_device_list_attached(self, vm, event, **kwargs):
        # pylint: disable=unused-argument,no-self-use
        if not vm.is_running():
            return

        for dev in self.get_all_devices(vm.app):
            if dev.frontend_domain == vm:
                yield (dev, {})

    @qubes.ext.handler('device-pre-attach:usb')
    @asyncio.coroutine
    def on_device_attach_usb(self, vm, event, device, options):
        # pylint: disable=unused-argument
        if not vm.is_running() or vm.qid == 0:
            return

        if not isinstance(device, USBDevice):
            return

        if options:
            raise qubes.exc.QubesException(
                'USB device attach do not support options')

        if device.frontend_domain:
            raise qubes.devices.DeviceAlreadyAttached(
                'Device {!s} already attached to {!s}'.format(device,
                    device.frontend_domain)
            )

        stubdom_qrexec = (vm.virt_mode == 'hvm' and \
            vm.features.check_with_template('stubdom_qrexec', False))

        name = vm.name + '-dm' if stubdom_qrexec else vm.name

        # update the cache before the call, to avoid sending duplicated events
        # (one on qubesdb watch and the other by the caller of this method)
        self.devices_cache[device.backend_domain.name][device.ident] = vm

        # set qrexec policy to allow this device
        policy_line = '{} {} allow,user=root\n'.format(name,
            device.backend_domain.name)
        modify_qrexec_policy('qubes.USB+{}'.format(device.ident),
            policy_line, True)
        try:
            # and actual attach
            try:
                yield from vm.run_service_for_stdio('qubes.USBAttach',
                    user='root', stubdom=stubdom_qrexec,
                    input='{} {}\n'.format(device.backend_domain.name,
                        device.ident).encode())
            except subprocess.CalledProcessError as e:
                if e.returncode == 127:
                    raise USBProxyNotInstalled(
                        "qubes-usb-proxy not installed in the VM")
                else:
                    # TODO: sanitize and include stdout
                    sanitized_stderr = ''.join(
                        [chr(c) for c in e.stderr if 0x20 <= c < 0x80])
                    raise QubesUSBException(
                        'Device attach failed: {}'.format(sanitized_stderr))
        finally:
            modify_qrexec_policy('qubes.USB+{}'.format(device.ident),
                policy_line, False)

    @qubes.ext.handler('device-pre-detach:usb')
    @asyncio.coroutine
    def on_device_detach_usb(self, vm, event, device):
        # pylint: disable=unused-argument,no-self-use
        if not vm.is_running() or vm.qid == 0:
            return

        if not isinstance(device, USBDevice):
            return

        connected_to = device.frontend_domain
        # detect race conditions; there is still race here, but much smaller
        if connected_to is None or connected_to.qid != vm.qid:
            raise QubesUSBException(
                "Device {!s} not connected to VM {}".format(
                    device, vm.name))

        # update the cache before the call, to avoid sending duplicated events
        # (one on qubesdb watch and the other by the caller of this method)
        self.devices_cache[device.backend_domain.name][device.ident] = None

        try:
            yield from device.backend_domain.run_service_for_stdio(
                'qubes.USBDetach',
                user='root',
                input='{}\n'.format(device.ident).encode())
        except subprocess.CalledProcessError as e:
            # TODO: sanitize and include stdout
            raise QubesUSBException('Device detach failed')

    @qubes.ext.handler('domain-start')
    @asyncio.coroutine
    def on_domain_start(self, vm, _event, **_kwargs):
        # pylint: disable=unused-argument
        for assignment in vm.devices['usb'].assignments(persistent=True):
            device = assignment.device
            yield from self.on_device_attach_usb(vm, '', device, options={})

    @qubes.ext.handler('domain-shutdown')
    @asyncio.coroutine
    def on_domain_shutdown(self, vm, _event, **_kwargs):
        # pylint: disable=unused-argument
        vm.fire_event('device-list-change:usb')

    @qubes.ext.handler('qubes-close', system=True)
    def on_qubes_close(self, app, event):
        # pylint: disable=unused-argument
        self.devices_cache.clear()
