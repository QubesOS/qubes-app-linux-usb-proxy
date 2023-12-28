#!/usr/bin/python3 -O
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
import base64
import collections
import fcntl
import grp
import itertools
import os
import re
import string
import subprocess
import sys

import tempfile
from enum import Enum
from typing import List, Optional, Dict, Tuple

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
        super(USBDevice, self).__init__(
            backend_domain=backend_domain, ident=ident, devclass="usb")

        self._qdb_ident = ident.replace('.', '_')
        self._qdb_path = '/qubes-usb-devices/' + self._qdb_ident

    @property
    def vendor(self) -> str:
        """
        Device vendor from local database `/usr/share/hwdata/usb.ids`

        Could be empty string or "unknown".

        Lazy loaded.
        """
        if self._vendor is None:
            result = self._load_desc_from_qubesdb()["vendor"]
        else:
            result = self._vendor
        return result

    @property
    def product(self) -> str:
        """
        Device name from local database `/usr/share/hwdata/usb.ids`

        Could be empty string or "unknown".

        Lazy loaded.
        """
        if self._product is None:
            result = self._load_desc_from_qubesdb()["product"]
        else:
            result = self._product
        return result

    @property
    def manufacturer(self) -> str:
        """
        The name of the manufacturer of the device introduced by device itself

        Could be empty string or "unknown".

        Lazy loaded.
        """
        if self._manufacturer is None:
            result = self._load_desc_from_qubesdb()["manufacturer"]
        else:
            result = self._manufacturer
        return result

    @property
    def name(self) -> str:
        """
        The name of the device it introduced itself with (could be empty string)

        Could be empty string or "unknown".

        Lazy loaded.
        """
        if self._name is None:
            result = self._load_desc_from_qubesdb()["name"]
        else:
            result = self._name
        return result

    @property
    def serial(self) -> str:
        """
        The serial number of the device it introduced itself with.

        Could be empty string or "unknown".

        Lazy loaded.
        """
        if self._serial is None:
            result = self._load_desc_from_qubesdb()["serial"]
        else:
            result = self._serial
        return result

    @property
    def interfaces(self) -> List[qubes.devices.DeviceInterface]:
        """
        List of device interfaces.

        Every device should have at least one interface.
        """
        if self._interfaces is None:
            result = self._load_interfaces_from_qubesdb()
        else:
            result = self._interfaces
        return result

    @property
    def parent_device(self) -> Optional[qubes.devices.DeviceInfo]:
        """
        The parent device if any.

        USB device has no parents.
        """
        return None

    # @property
    # def port_id(self) -> str:
    #     """
    #     Which port the device is connected to.
    #     """
    #     return self.ident.split("-")[1]

    def _load_interfaces_from_qubesdb(self) \
            -> List[qubes.devices.DeviceInterface]:
        result = [qubes.devices.DeviceInterface.Other]
        if not self.backend_domain.is_running():
            # don't cache this value
            return result
        untrusted_interfaces: bytes = (
            self.backend_domain.untrusted_qdb.read(
                self._qdb_path + '/interfaces')
        )
        if not untrusted_interfaces:
            return result
        self._interfaces = result = [
            qubes.devices.DeviceInterface.from_str(
                self._sanitize(ifc, safe_chars=string.hexdigits)
            )
            for ifc in untrusted_interfaces.split(b':')
            if ifc
        ]
        return result

    def _load_desc_from_qubesdb(self) -> Dict[str, str]:
        unknown = "unknown"
        result = {"vendor": unknown,
                  "product": unknown,
                  "manufacturer": unknown,
                  "name": unknown,
                  "serial": unknown}
        if not self.backend_domain.is_running():
            # don't cache this value
            return result
        untrusted_device_desc: bytes = (
            self.backend_domain.untrusted_qdb.read(
                self._qdb_path + '/desc')
        )
        if not untrusted_device_desc:
            return result
        try:
            (untrusted_vendor_product, untrusted_manufacturer,
             untrusted_name, untrusted_serial
             ) = untrusted_device_desc.split(b' ')
            untrusted_vendor, untrusted_product = (
                untrusted_vendor_product.split(b':'))
        except ValueError:
            # desc doesn't contain correctly formatted data,
            # but it is not empty. We cannot parse it,
            # but we can still put it to the `serial` just to provide
            # some information to the user.
            untrusted_vendor, untrusted_product, untrusted_manufacturer = (
                unknown.encode(), unknown.encode(), unknown.encode())
            untrusted_name = untrusted_device_desc.replace(b' ', b'_')
        vendor, product = self._get_vendor_and_product_names(
            self._sanitize(untrusted_vendor),
            self._sanitize(untrusted_product),
        )
        self._desc_vendor = result["vendor"] = vendor
        self._desc_product = result["product"] = product
        self._desc_manufacturer = result["manufacturer"] = (
            self._sanitize(untrusted_manufacturer))
        self._desc_name = result["name"] = (
            self._sanitize(untrusted_name))
        return result

    @staticmethod
    def _sanitize(
            untrusted_device_desc: bytes,
            safe_chars: str =
            string.ascii_letters + string.digits + string.punctuation + ' '
    ) -> str:
        # b'USB\\x202.0\\x20Camera' -> 'USB 2.0 Camera'
        untrusted_device_desc = untrusted_device_desc.decode(
            'unicode_escape', errors='ignore')
        return ''.join(
            c if c in set(safe_chars) else '_' for c in untrusted_device_desc
        )

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
    def _get_vendor_and_product_names(
            vendor_id: str, product_id: str
    ) -> Tuple[str, str]:
        """
        Return tuple of vendor's and product's names for the ids.

        If the id is not known return ("unknown", "unknown").
        """
        return (USBDevice._load_usb_known_devices()
                .get(vendor_id, dict())
                .get(product_id, ("unknown", "unknown"))
                )

    @staticmethod
    def _load_usb_known_devices() -> Dict[str, Dict[str, Tuple[str, str]]]:
        """
        List of known device vendors, devices and interfaces.

        result[vendor_id][device_id] = (vendor_name, product_name)
        """
        # Syntax:
        # vendor  vendor_name                       <-- 2 spaces between
        #       device  device_name                 <-- single tab
        #               interface  interface_name   <-- two tabs
        # ...
        # C class  class_name
        #       subclass  subclass_name         <-- single tab
        #               prog-if  prog-if_name   <-- two tabs
        result = {}
        with open('/usr/share/hwdata/usb.ids',
                  encoding='utf-8', errors='ignore') as usb_ids:
            for line in usb_ids.readlines():
                line = line.rstrip()
                if line.startswith('#'):
                    # skip comments
                    continue
                elif not line:
                    # skip empty lines
                    continue
                elif line.startswith('\t\t'):
                    # skip interfaces
                    continue
                elif line.startswith('C '):
                    # description of classes starts here, we can finish
                    break
                elif line.startswith('\t'):
                    # save vendor, device pair
                    device_id, _, device_name = line[1:].split(' ', 2)
                    result[vendor_id][device_id] = vendor_name, device_name
                else:
                    # new vendor
                    vendor_id, _, vendor_name = line[:].split(' ', 2)
                    result[vendor_id] = {}

        return result


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
        # include dom0 devices in listing only when usb-proxy is really
        # installed there
        self.usb_proxy_installed_in_dom0 = os.path.exists(
            '/etc/qubes-rpc/qubes.USB')
        self.devices_cache = collections.defaultdict(dict)

    @qubes.ext.handler('domain-init', 'domain-load')
    def on_domain_init_load(self, vm, event):
        """Initialize watching for changes"""
        # pylint: disable=unused-argument,no-self-use
        vm.watch_qdb_path('/qubes-usb-devices')
        if event == 'domain-load':
            # avoid building a cache on domain-init, as it isn't fully set yet,
            # and definitely isn't running yet
            current_devices = {
                dev.ident: dev.frontend_domain
                for dev in self.on_device_list_usb(vm, None)
            }
            self.devices_cache[vm.name] = current_devices
            # TODO: fire device-added
        else:
            self.devices_cache[vm.name] = {}

    async def _attach_and_notify(self, vm, device, options):
        # bypass DeviceCollection logic preventing double attach
        await self.on_device_attach_usb(vm,
            'device-pre-attach:usb', device, options)
        await vm.fire_event_async('device-attach:usb',
                device=device,
                options=options)

    @qubes.ext.handler('domain-qdb-change:/qubes-usb-devices')
    def on_qdb_change(self, vm, event, path):
        """A change in QubesDB means a change in device list"""
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
        devices_cache_for_vm = self.devices_cache[vm.name]
        for dev_id, connected_to in current_devices.items():
            if dev_id not in devices_cache_for_vm:
                new_devices.add(dev_id)
                device = USBDevice(vm, dev_id)
                vm.fire_event('device-added:usb', device=device)
            elif devices_cache_for_vm[dev_id] != current_devices[dev_id]:
                if devices_cache_for_vm[dev_id] is not None:
                    disconnected_devices[dev_id] = devices_cache_for_vm[dev_id]
                if current_devices[dev_id] is not None:
                    connected_devices[dev_id] = current_devices[dev_id]
        for dev_id, connected_to in devices_cache_for_vm.items():
            if dev_id not in current_devices:
                device = USBDevice(vm, dev_id)
                vm.fire_event('device-removed:usb', device=device)

        self.devices_cache[vm.name] = current_devices
        # send events about devices detached/attached outside by themselves
        # (like device pulled out or manual qubes.USB qrexec call)
        for dev_ident, front_vm in disconnected_devices.items():
            dev_id = USBDevice(vm, dev_ident)
            asyncio.ensure_future(front_vm.fire_event_async('device-detach:usb',
                                                            device=dev_id))
        for dev_ident, front_vm in connected_devices.items():
            dev_id = USBDevice(vm, dev_ident)
            asyncio.ensure_future(front_vm.fire_event_async('device-attach:usb',
                                                            device=dev_id,
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
    async def on_device_attach_usb(self, vm, event, device, options):
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
            vm.features.check_with_template('stubdom-qrexec', False))

        name = vm.name + '-dm' if stubdom_qrexec else vm.name

        extra_kwargs = {}
        if stubdom_qrexec:
            extra_kwargs['stubdom'] = True

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
                await vm.run_service_for_stdio('qubes.USBAttach',
                    user='root',
                    input='{} {}\n'.format(device.backend_domain.name,
                        device.ident).encode(), **extra_kwargs)
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
    async def on_device_detach_usb(self, vm, event, device):
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
            await device.backend_domain.run_service_for_stdio(
                'qubes.USBDetach',
                user='root',
                input='{}\n'.format(device.ident).encode())
        except subprocess.CalledProcessError as e:
            # TODO: sanitize and include stdout
            raise QubesUSBException('Device detach failed')

    @qubes.ext.handler('domain-start')
    async def on_domain_start(self, vm, _event, **_kwargs):
        # pylint: disable=unused-argument
        for assignment in vm.devices['usb'].assignments(persistent=True):
            device = assignment.device
            await self.on_device_attach_usb(vm, '', device, options={})

    @qubes.ext.handler('domain-shutdown')
    async def on_domain_shutdown(self, vm, _event, **_kwargs):
        # pylint: disable=unused-argument
        vm.fire_event('device-list-change:usb')

    @qubes.ext.handler('qubes-close', system=True)
    def on_qubes_close(self, app, event):
        # pylint: disable=unused-argument
        self.devices_cache.clear()
