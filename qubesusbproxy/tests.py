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
import qubes.tests.extra
import time

core2 = False
core3 = False
try:
    import qubesusbproxy.core3ext
    core3 = True
except ImportError:
    pass

try:
    import qubes.qubesutils
    core2 = True
except ImportError:
    pass


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

def create_usb_gadget(vm):
    vm.start()
    p = vm.run(GADGET_PREREQ, user="root",
        passio_popen=True, passio_stderr=True)
    (_, stderr) = p.communicate()
    if p.returncode != 0:
        raise unittest.SkipTest("missing USB Gadget subsystem")
    p = vm.run(GADGET_PREPARE, user="root",
        passio_popen=True, passio_stderr=True)
    (_, stderr) = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("Failed to setup USB gadget: " + stderr)
    p = vm.run(
        'ls /sys/bus/platform/devices/dummy_hcd.0/usb*|grep -x .-.',
        passio_popen=True)
    (stdout, _) = p.communicate()
    stdout = stdout.strip()
    if not stdout:
        raise RuntimeError("Failed to get dummy device ID")
    return stdout

def remove_usb_gadget(vm):
    assert vm.is_running()

    retcode = vm.run("echo > /sys/kernel/config/usb_gadget/test_g1/UDC",
        user="root", wait=True)
    if retcode != 0:
        raise RuntimeError("Failed to disable USB gadget")

class TC_00_USBProxy(qubes.tests.extra.ExtraTestCase):
    def setUp(self):
        super(TC_00_USBProxy, self).setUp()
        vms = self.create_vms(["backend", "frontend"])
        (self.backend, self.frontend) = vms
        self.qrexec_policy('qubes.USB', self.frontend.name, self.backend.name)
        self.dummy_usb_dev = create_usb_gadget(self.backend)

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

    def test_020_detach_on_remove(self):
        self.frontend.start()
        self.assertEqual(self.frontend.run_service('qubes.USBAttach',
            user='root',
            input="{} {}".format(self.backend.name, self.dummy_usb_dev)), 0,
            "qubes.USBAttach call failed")
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")
        remove_usb_gadget(self.backend)
        self.assertEqual(self.frontend.run('lsusb -d 1234:1234', wait=True), 1,
            "Device not cleaned up")
        # TODO: check for kernel errors?

class TC_10_USBProxy_core2(qubes.tests.extra.ExtraTestCase):
    def setUp(self):
        super(TC_10_USBProxy_core2, self).setUp()
        vms = self.create_vms(["backend", "frontend"])
        (self.backend, self.frontend) = vms
        self.qrexec_policy('qubes.USB', self.frontend.name, self.backend.name)
        self.dummy_usb_dev = create_usb_gadget(self.backend)
        self.usbdev_name = '{}:{}'.format(self.backend.name, self.dummy_usb_dev)

    def test_000_list_all(self):
        usb_list = qubes.qubesutils.usb_list(self.qc)
        self.assertIn(self.usbdev_name, usb_list)

    def test_010_list_vm(self):
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertIn(self.usbdev_name, usb_list)

    def test_020_attach(self):
        self.frontend.start()
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        try:
            qubes.qubesutils.usb_attach(self.qc,
                self.frontend, usb_list[self.usbdev_name])
        except qubes.qubesutils.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")

        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertEquals(usb_list[self.usbdev_name]['connected-to'],
            self.frontend)

    def test_030_detach(self):
        self.frontend.start()
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        try:
            qubes.qubesutils.usb_attach(self.qc, self.frontend,
                usb_list[self.usbdev_name])
        except qubes.qubesutils.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        qubes.qubesutils.usb_detach(self.qc, self.frontend,
            usb_list[self.usbdev_name])
        # FIXME: usb-export script may update qubesdb with 1sec delay
        time.sleep(2)

        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertIsNone(usb_list[self.usbdev_name]['connected-to'])

        self.assertNotEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device disconnection failed")

    def test_040_detach_all(self):
        self.frontend.start()
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        try:
            qubes.qubesutils.usb_attach(self.qc, self.frontend,
                usb_list[self.usbdev_name])
        except qubes.qubesutils.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        qubes.qubesutils.usb_detach_all(self.qc, self.frontend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        time.sleep(2)

        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertIsNone(usb_list[self.usbdev_name]['connected-to'])

        self.assertNotEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device disconnection failed")

    def test_050_list_attached(self):
        """ Attached device should not be listed as further attachable """
        self.frontend.start()
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)

        usb_list_front_pre = qubes.qubesutils.usb_list(self.qc,
            vm=self.frontend)

        try:
            qubes.qubesutils.usb_attach(self.qc,
                self.frontend, usb_list[self.usbdev_name])
        except qubes.qubesutils.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")

        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertEquals(usb_list[self.usbdev_name]['connected-to'],
            self.frontend)

        usb_list_front_post = qubes.qubesutils.usb_list(self.qc,
            vm=self.frontend)

        self.assertEquals(usb_list_front_pre, usb_list_front_post)

    def test_060_auto_detach_on_remove(self):
        self.frontend.start()
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        try:
            qubes.qubesutils.usb_attach(self.qc, self.frontend,
                usb_list[self.usbdev_name])
        except qubes.qubesutils.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        time.sleep(1)

        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        self.assertNotIn(self.usbdev_name, usb_list)
        self.assertNotEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device disconnection failed")

    def test_070_attach_not_installed_front(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.frontend.run("rm -f /etc/qubes-rpc/qubes.USBAttach",
            user="root", wait=True)
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        with self.assertRaises(qubes.qubesutils.USBProxyNotInstalled):
            qubes.qubesutils.usb_attach(self.qc, self.frontend,
                usb_list[self.usbdev_name])

    def test_075_attach_not_installed_back(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.backend.run("rm -f /etc/qubes-rpc/qubes.USB",
            user="root", wait=True)
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        usb_list = qubes.qubesutils.usb_list(self.qc, vm=self.backend)
        with self.assertRaises(qubes.qubesutils.USBProxyNotInstalled):
            qubes.qubesutils.usb_attach(self.qc, self.frontend,
                usb_list[self.usbdev_name])


class TC_20_USBProxy_core3(qubes.tests.extra.ExtraTestCase):
    # noinspection PyAttributeOutsideInit
    def setUp(self):
        super(TC_20_USBProxy_core3, self).setUp()
        vms = self.create_vms(["backend", "frontend"])
        (self.backend, self.frontend) = vms
        self.qrexec_policy('qubes.USB', self.frontend.name, self.backend.name)
        self.usbdev_ident = create_usb_gadget(self.backend)
        self.usbdev_name = '{}:{}'.format(self.backend.name, self.usbdev_ident)

    def test_000_list(self):
        usb_list = self.backend.devices['usb']
        self.assertIn(self.usbdev_name, [str(dev) for dev in usb_list])

    def test_010_attach_offline(self):
        usb_dev = self.backend.devices['usb'][self.usbdev_ident]
        self.frontend.devices['usb'].attach(usb_dev)
        self.assertIsNone(usb_dev.frontend_domain)
        try:
            self.frontend.start()
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")

        self.assertEquals(usb_dev.frontend_domain,
            self.frontend)

    def test_020_attach(self):
        self.frontend.start()
        usb_dev = self.backend.devices['usb'][self.usbdev_ident]
        try:
            self.frontend.devices['usb'].attach(usb_dev)
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")

        self.assertEquals(usb_dev.frontend_domain,
            self.frontend)

    def test_030_detach(self):
        self.frontend.start()
        usb_dev = self.backend.devices['usb'][self.usbdev_ident]
        try:
            self.frontend.devices['usb'].attach(usb_dev)
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.frontend.devices['usb'].detach(usb_dev)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        time.sleep(2)

        self.assertIsNone(usb_dev.frontend_domain)

        self.assertNotEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device disconnection failed")

    def test_040_detach_offline(self):
        usb_dev = self.backend.devices['usb'][self.usbdev_ident]
        self.frontend.devices['usb'].attach(usb_dev)
        self.assertIsNone(usb_dev.frontend_domain)
        self.frontend.devices['usb'].detach(usb_dev)
        self.assertIsNone(usb_dev.frontend_domain)

    def test_050_list_attached(self):
        """ Attached device should not be listed as further attachable """
        self.frontend.start()
        usb_list = self.backend.devices['usb']

        usb_list_front_pre = list(self.frontend.devices['usb'])

        try:
            self.frontend.devices['usb'].attach(usb_list[self.usbdev_ident])
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device connection failed")

        self.assertEquals(usb_list[self.usbdev_ident].frontend_domain,
            self.frontend)

        usb_list_front_post = list(self.frontend.devices['usb'])

        self.assertEquals(usb_list_front_pre, usb_list_front_post)

    def test_060_auto_detach_on_remove(self):
        self.frontend.start()
        usb_list = self.backend.devices['usb']
        try:
            self.frontend.devices['usb'].attach(usb_list[self.usbdev_ident])
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        time.sleep(1)

        self.assertNotIn(self.usbdev_name, [str(dev) for dev in usb_list])
        self.assertNotEqual(self.frontend.run('lsusb -d 1234:1234',
            wait=True), 0,
            "Device disconnection failed")

    def test_070_attach_not_installed_front(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.frontend.run("rm -f /etc/qubes-rpc/qubes.USBAttach",
            user="root", wait=True)
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        usb_list = self.backend.devices['usb']
        with self.assertRaises(qubesusbproxy.core3ext.USBProxyNotInstalled):
            self.frontend.devices['usb'].attach(usb_list[self.usbdev_ident])

    def test_075_attach_not_installed_back(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.backend.run("rm -f /etc/qubes-rpc/qubes.USB",
            user="root", wait=True)
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        usb_list = self.backend.devices['usb']
        with self.assertRaises(qubesusbproxy.core3ext.USBProxyNotInstalled):
            self.frontend.devices['usb'].attach(usb_list[self.usbdev_ident])


def list_tests():
    tests = [TC_00_USBProxy]
    if core2:
        tests += [TC_10_USBProxy_core2]
    if core3:
        tests += [TC_20_USBProxy_core3]
    return tests
