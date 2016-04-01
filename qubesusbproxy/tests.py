#!/usr/bin/python
# vim: fileencoding=utf-8

#
# The Qubes OS Project, https://www.qubes-os.org/
#
# Copyright (C) 2016
#                   Marek Marczykowski-Górecki <marmarek@invisiblethingslab.com>
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
import unittest

# if this fails, skip the test (missing software/configuration in the VM)
import time

GADGET_PREREQ = '&&'.join([
    "modprobe usb_f_mass_storage",
    "mount|grep -q configfs",
    "test -d /sys/class/udc/dummy_udc.0",
])

GADGET_PREPARE = ';'.join([
    "set -e -x",
    "cd /sys/kernel/config/usb_gadget",
    "mkdir test_g1; cd test_g1",
    "echo 0x1234 > idProduct",
    "echo 0x1234 > idVendor",
    "mkdir strings/0x409",
    "echo 0123456789 > strings/0x409/serialnumber",
    "echo Qubes > strings/0x409/manufacturer",
    "echo Test device > strings/0x409/product",
    "mkdir configs/c.1",
    "mkdir functions/mass_storage.ms1",
    "truncate -s 512M /var/tmp/test-file",
    "echo /var/tmp/test-file > functions/mass_storage.ms1/lun.0/file",
    "ln -s functions/mass_storage.ms1 configs/c.1",
    "echo dummy_udc.0 > UDC",
    "sleep 2; udevadm settle",
])


class TC_00_USBProxy(unittest.TestCase):
    def setUp(self):
        super(TC_00_USBProxy, self).setUp()
        vms = self.create_vms(["backend", "frontend"])
        (self.backend, self.frontend) = vms
        self.qrexec_policy('qubes.USB', self.frontend.name, self.backend.name)
        self.backend.start()
        p = self.backend.run(GADGET_PREREQ, user="root",
            passio_popen=True, passio_stderr=True)
        (_, stderr) = p.communicate()
        if p.returncode != 0:
            self.skipTest("missing USB Gadget subsystem")
        p = self.backend.run(GADGET_PREPARE, user="root",
            passio_popen=True, passio_stderr=True)
        (_, stderr) = p.communicate()
        if p.returncode != 0:
            raise RuntimeError("Failed to setup USB gadget: " + stderr)
        p = self.backend.run(
            'ls /sys/bus/platform/devices/dummy_hcd.0/usb*|grep -x .-.',
            passio_popen=True)
        (stdout, _) = p.communicate()
        stdout = stdout.strip()
        if not stdout:
            raise RuntimeError("Failed to get dummy device ID")
        self.dummy_usb_dev = stdout

    def test_000_attach_detach(self):
        self.frontend.start()
        # TODO: check qubesdb entries
        self.assertEqual(self.frontend.run_service('qubes.USBAttach',
            user='root',
            input="{} {}".format(self.backend.name, self.dummy_usb_dev)), 0,
            "qubes.USBAttach call failed")
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")
        # TODO: check qubesdb entries
        self.assertEqual(self.frontend.run_service('qubes.USBDetach',
            user='root',
            input="{} {}".format(self.backend.name, self.dummy_usb_dev)), 0,
            "qubes.USBDetach call failed")
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234', wait=True), 1,
            "Device disconnection failed")

    def test_010_attach_detach_vid_pid(self):
        self.frontend.start()
        # TODO: check qubesdb entries
        self.assertEqual(self.frontend.run_service('qubes.USBAttach',
            user='root',
            input="{} {}".format(self.backend.name, "0x1234.0x1234")), 0,
            "qubes.USBAttach call failed")
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234', wait=True), 0,
            "Device connection failed")
        # TODO: check qubesdb entries
        self.assertEqual(self.frontend.run_service('qubes.USBDetach',
            user='root',
            input="{} {}".format(self.backend.name, "0x1234.0x1234")), 0,
            "qubes.USBDetach call failed")
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234', wait=True), 1,
            "Device disconnection failed")


def list_tests():
    return (
        TC_00_USBProxy,
    )
