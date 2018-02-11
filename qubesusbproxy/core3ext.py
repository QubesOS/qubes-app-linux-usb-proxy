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
import os
import re
import string
import subprocess

import errno

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


class USBDeviceExtension(qubes.ext.Extension):

    def __init__(self):
        super(USBDeviceExtension, self).__init__()
        #include dom0 devices in listing only when usb-proxy is really
        # installed there
        self.usb_proxy_installed_in_dom0 = os.path.exists(
            '/etc/qubes-rpc/qubes.USB')

    @qubes.ext.handler('domain-init', 'domain-load')
    def on_domain_init_load(self, vm, event):
        '''Initialize watching for changes'''
        # pylint: disable=unused-argument,no-self-use
        vm.watch_qdb_path('/qubes-usb-devices')

    @qubes.ext.handler('domain-qdb-change:/qubes-usb-devices')
    def on_qdb_change(self, vm, event, path):
        '''A change in QubesDB means a change in device list'''
        # pylint: disable=unused-argument,no-self-use
        vm.fire_event('device-list-change:usb')

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

        # set qrexec policy to allow this device
        policy_line = '{} {} allow,user=root\n'.format(vm.name,
            device.backend_domain.name)
        policy_path = '/etc/qubes-rpc/policy/qubes.USB+{}'.format(
            device.ident)
        policy_exists = os.path.exists(policy_path)
        if not policy_exists:
            try:
                fd = os.open(policy_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, 'w') as f:
                    f.write(policy_line)
            except OSError as e:
                if e.errno == errno.EEXIST:
                    pass
                else:
                    raise
        else:
            with open(policy_path, 'r+') as f:
                policy = f.readlines()
                policy.insert(0, policy_line)
                f.truncate(0)
                f.seek(0)
                f.write(''.join(policy))
        try:
            # and actual attach
            try:
                yield from vm.run_service_for_stdio('qubes.USBAttach',
                    user='root',
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
            # FIXME: there is a race condition here - some other process might
            # modify the file in the meantime. This may result in unexpected
            # denials, but will not allow too much
            if not policy_exists:
                os.unlink(policy_path)
            else:
                with open(policy_path, 'r+') as f:
                    policy = f.readlines()
                    policy.remove(
                        '{} {} allow\n'.format(vm.name,
                            device.backend_domain.name))
                    f.truncate(0)
                    f.seek(0)
                    f.write(''.join(policy))

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
