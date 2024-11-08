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
import time
import unittest
from unittest import mock
from unittest.mock import Mock, AsyncMock

import jinja2

import qubes.tests.extra

core3 = False
LEGACY = False
try:
    import qubesusbproxy.core3ext
    import asyncio

    try:
        from qubes.device_protocol import DeviceAssignment, VirtualDevice, Port

        def make_assignment(backend, ident, auto_attach=False):
            return DeviceAssignment(
                VirtualDevice(Port(backend, ident, "usb")),
                mode="auto-attach" if auto_attach else "manual",
            )

        def assign(test, collection, assignment):
            test.loop.run_until_complete(collection.assign(assignment))

        def unassign(test, collection, assignment):
            test.loop.run_until_complete(collection.unassign(assignment))

    except ImportError:
        # This extension supports both the legacy and new device API.
        # In the case of the legacy backend, functionality is limited.
        from qubes.devices import DeviceAssignment

        def make_assignment(backend, ident, required=False):
            # pylint: disable=unexpected-keyword-arg
            return DeviceAssignment(backend, ident, persistent=required)

        def assign(test, collection, assignment):
            test.loop.run_until_complete(collection.attach(assignment))

        def unassign(test, collection, assignment):
            test.loop.run_until_complete(collection.detach(assignment))

        LEGACY = True

    core3 = True
except ImportError:
    pass

is_r40 = False
try:
    with open("/etc/qubes-release") as f:
        if "R4.0" in f.read():
            is_r40 = True
except FileNotFoundError:
    pass

GADGET_PREREQ = "&&".join(
    [
        "modprobe dummy_hcd",
        "modprobe usb_f_mass_storage",
        "mount|grep -q configfs",
        "test -d /sys/class/udc/dummy_udc.0",
    ]
)

GADGET_PREPARE = ";".join(
    [
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
    ]
)


def create_usb_gadget(vm):
    vm.start()
    p = vm.run(
        GADGET_PREREQ, user="root", passio_popen=True, passio_stderr=True
    )
    (_, _stderr) = p.communicate()
    if p.returncode != 0:
        raise unittest.SkipTest("missing USB Gadget subsystem")
    p = vm.run(
        GADGET_PREPARE, user="root", passio_popen=True, passio_stderr=True
    )
    (_, stderr) = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("Failed to setup USB gadget: " + stderr.decode())
    p = vm.run(
        "ls /sys/bus/platform/devices/dummy_hcd.0/usb*|grep -x .-.",
        passio_popen=True,
    )
    (stdout, _) = p.communicate()
    stdout = stdout.strip()
    if not stdout:
        raise RuntimeError("Failed to get dummy device ID")
    return stdout


def remove_usb_gadget(vm):
    assert vm.is_running()

    retcode = vm.run(
        "echo > /sys/kernel/config/usb_gadget/test_g1/UDC",
        user="root",
        wait=True,
    )
    if retcode != 0:
        raise RuntimeError("Failed to disable USB gadget")


def recreate_usb_gadget(vm):
    """Re-create the gadget previously created with *create_usb_gadget*,
    then removed with *remove_usb_gadget*.
    """

    reconnect = ";".join(
        [
            "cd /sys/kernel/config/usb_gadget",
            "mkdir test_g1; cd test_g1",
            "echo dummy_udc.0 > UDC",
            "sleep 2; udevadm settle",
        ]
    )

    p = vm.run(reconnect, user="root", passio_popen=True, passio_stderr=True)
    (_, stderr) = p.communicate()
    if p.returncode != 0:
        raise RuntimeError("Failed to re-create USB gadget: " + stderr.decode())


class TC_00_USBProxy(qubes.tests.extra.ExtraTestCase):
    def setUp(self):
        if "whonix-gw" in self.template:
            self.skipTest("whonix-gw does not have qubes-usb-proxy")
        super().setUp()
        vms = self.create_vms(["backend", "frontend"])
        (self.backend, self.frontend) = vms
        self.qrexec_policy("qubes.USB", self.frontend.name, self.backend.name)
        self.dummy_usb_dev = create_usb_gadget(self.backend).decode()

    def test_000_attach_detach(self):
        self.frontend.start()
        # TODO: check qubesdb entries
        self.assertEqual(
            self.frontend.run_service(
                "qubes.USBAttach",
                user="root",
                input=f"{self.backend.name} {self.dummy_usb_dev}\n",
            ),
            0,
            "qubes.USBAttach call failed",
        )
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )
        # TODO: check qubesdb entries
        self.assertEqual(
            self.frontend.run_service(
                "qubes.USBDetach",
                user="root",
                input=f"{self.backend.name} {self.dummy_usb_dev}\n",
            ),
            0,
            "qubes.USBDetach call failed",
        )
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            1,
            "Device disconnection failed",
        )

    def test_010_attach_detach_vid_pid(self):
        self.frontend.start()
        # TODO: check qubesdb entries
        self.assertEqual(
            self.frontend.run_service(
                "qubes.USBAttach",
                user="root",
                input=f"{self.backend.name} 0x1234.0x1234\n",
            ),
            0,
            "qubes.USBAttach call failed",
        )
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )
        # TODO: check qubesdb entries
        self.assertEqual(
            self.frontend.run_service(
                "qubes.USBDetach",
                user="root",
                input=f"{self.backend.name} 0x1234.0x1234\n",
            ),
            0,
            "qubes.USBDetach call failed",
        )
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            1,
            "Device disconnection failed",
        )

    def test_020_detach_on_remove(self):
        self.frontend.start()
        self.assertEqual(
            self.frontend.run_service(
                "qubes.USBAttach",
                user="root",
                input=f"{self.backend.name} {self.dummy_usb_dev}\n",
            ),
            0,
            "qubes.USBAttach call failed",
        )
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )
        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb/disconnect with 1sec delay
        time.sleep(2)
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            1,
            "Device not cleaned up",
        )
        # TODO: check for kernel errors?


class TC_20_USBProxy_core3(qubes.tests.extra.ExtraTestCase):
    # noinspection PyAttributeOutsideInit
    def setUp(self):
        super().setUp()
        self.backend, self.frontend = self.create_vms(["backend", "frontend"])
        self.qrexec_policy("qubes.USB", self.frontend.name, self.backend.name)
        self.usbdev_ident = create_usb_gadget(self.backend).decode()
        self.usbdev_name = (
            f"{self.backend.name}:{self.usbdev_ident}"
            ":1234:1234:0123456789:u080650"
        )

    def tearDown(self):
        # remove vms in this specific order, otherwise there may remain stray
        #  dependency between them (so, objects leaks)
        self.remove_vms((self.frontend, self.backend))

        super().tearDown()

    def test_000_list(self):
        usb_list = self.backend.devices["usb"]
        self.assertIn(self.usbdev_name, [str(dev) for dev in usb_list])

    def test_010_assign(self):
        usb_dev = self.backend.devices["usb"][self.usbdev_ident]
        ass = make_assignment(self.backend, self.usbdev_ident, auto_attach=True)
        assign(self, self.frontend.devices["usb"], ass)
        self.assertIsNone(usb_dev.attachment)
        try:
            self.frontend.start()
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )

        self.assertEqual(usb_dev.attachment, self.frontend)

    @unittest.mock.patch("qubes.ext.utils.confirm_device_attachment")
    @unittest.skipIf(LEGACY, "new feature")
    def test_011_assign_ask(self, confirm):
        confirm.return_value = self.frontend.name
        usb_dev = self.backend.devices["usb"][self.usbdev_ident]
        ass = DeviceAssignment(
            VirtualDevice(Port(self.backend, self.usbdev_ident, "usb")),
            mode="ask-to-attach",
        )
        assign(self, self.frontend.devices["usb"], ass)
        self.assertIsNone(usb_dev.attachment)
        try:
            self.frontend.start()
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )

        self.assertEqual(usb_dev.attachment, self.frontend)

    def test_020_attach(self):
        self.frontend.start()
        usb_dev = self.backend.devices["usb"][self.usbdev_ident]
        ass = make_assignment(self.backend, self.usbdev_ident)
        try:
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )

        self.assertEqual(usb_dev.attachment, self.frontend)

    def test_030_detach(self):
        self.frontend.start()
        usb_dev = self.backend.devices["usb"][self.usbdev_ident]
        ass = make_assignment(self.backend, self.usbdev_ident)
        try:
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.loop.run_until_complete(self.frontend.devices["usb"].detach(ass))
        # FIXME: usb-export script may update qubesdb with 1sec delay
        self.loop.run_until_complete(asyncio.sleep(2))

        self.assertIsNone(usb_dev.attachment)

        self.assertNotEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device disconnection failed",
        )

    def test_040_unassign(self):
        usb_dev = self.backend.devices["usb"][self.usbdev_ident]
        ass = make_assignment(self.backend, self.usbdev_ident, auto_attach=True)
        assign(self, self.frontend.devices["usb"], ass)
        self.assertIsNone(usb_dev.attachment)
        unassign(self, self.frontend.devices["usb"], ass)
        self.assertIsNone(usb_dev.attachment)

    def test_050_list_attached(self):
        """Attached device should not be listed as further attachable"""
        self.frontend.start()
        usb_list = self.backend.devices["usb"]

        usb_list_front_pre = list(self.frontend.devices["usb"])
        ass = make_assignment(self.backend, self.usbdev_ident)

        try:
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )

        self.assertEqual(usb_list[self.usbdev_ident].attachment, self.frontend)

        usb_list_front_post = list(self.frontend.devices["usb"])

        self.assertEqual(usb_list_front_pre, usb_list_front_post)

    def test_060_auto_detach_on_remove(self):
        self.frontend.start()
        usb_list = self.backend.devices["usb"]
        ass = make_assignment(self.backend, self.usbdev_ident)
        try:
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        self.loop.run_until_complete(asyncio.sleep(2))

        self.assertNotIn(self.usbdev_name, [str(dev) for dev in usb_list])
        self.assertNotEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device disconnection failed",
        )

    def test_061_auto_attach_on_reconnect(self):
        self.frontend.start()
        usb_list = self.backend.devices["usb"]
        ass = make_assignment(self.backend, self.usbdev_ident, auto_attach=True)
        try:
            assign(self, self.frontend.devices["usb"], ass)
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        timeout = 5
        while self.usbdev_name in (str(dev) for dev in usb_list):
            self.loop.run_until_complete(asyncio.sleep(1))
            timeout -= 1
            self.assertGreater(timeout, 0, "timeout on device remove")

        recreate_usb_gadget(self.backend)
        timeout = 5
        while self.usbdev_name not in (str(dev) for dev in usb_list):
            self.loop.run_until_complete(asyncio.sleep(1))
            timeout -= 1
            self.assertGreater(timeout, 0, "timeout on device create")
        self.loop.run_until_complete(asyncio.sleep(5))
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device reconnection failed",
        )

    def test_062_ask_to_attach_on_start(self):
        self.frontend.start()
        usb_list = self.backend.devices["usb"]
        ass = make_assignment(self.backend, self.usbdev_ident, auto_attach=True)
        try:
            assign(self, self.frontend.devices["usb"], ass)
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        remove_usb_gadget(self.backend)
        # FIXME: usb-export script may update qubesdb with 1sec delay
        timeout = 5
        while self.usbdev_name in (str(dev) for dev in usb_list):
            self.loop.run_until_complete(asyncio.sleep(1))
            timeout -= 1
            self.assertGreater(timeout, 0, "timeout on device remove")

        recreate_usb_gadget(self.backend)
        timeout = 5
        while self.usbdev_name not in (str(dev) for dev in usb_list):
            self.loop.run_until_complete(asyncio.sleep(1))
            timeout -= 1
            self.assertGreater(timeout, 0, "timeout on device create")
        self.loop.run_until_complete(asyncio.sleep(5))
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device reconnection failed",
        )

    def test_070_attach_not_installed_front(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.frontend.run(
            "rm -f /etc/qubes-rpc/qubes.USBAttach", user="root", wait=True
        )
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        ass = make_assignment(self.backend, self.usbdev_ident)
        with self.assertRaises(qubesusbproxy.core3ext.USBProxyNotInstalled):
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )

    @unittest.expectedFailure
    def test_075_attach_not_installed_back(self):
        self.frontend.start()
        # simulate package not installed
        retcode = self.backend.run(
            "rm -f /etc/qubes-rpc/qubes.USB", user="root", wait=True
        )
        if retcode != 0:
            raise RuntimeError("Failed to simulate not installed package")
        ass = make_assignment(self.backend, self.usbdev_ident)
        try:
            with self.assertRaises(qubesusbproxy.core3ext.USBProxyNotInstalled):
                self.loop.run_until_complete(
                    self.frontend.devices["usb"].attach(ass)
                )
        except qubesusbproxy.core3ext.QubesUSBException as e:
            self.fail(
                "Generic exception raise instead of specific "
                "USBProxyNotInstalled: " + str(e)
            )

    def test_080_attach_existing_policy(self):
        self.frontend.start()
        # this override policy file, but during normal execution it shouldn't
        # exist, so should be ok, especially on a testing system
        with open(
            f"/etc/qubes-rpc/policy/qubes.USB+{self.usbdev_ident}", "w+"
        ) as policy_file:
            policy_file.write("# empty policy\n")
        ass = make_assignment(self.backend, self.usbdev_ident)
        self.loop.run_until_complete(self.frontend.devices["usb"].attach(ass))

    @unittest.skipIf(is_r40, "Not supported on R4.0")
    def test_090_attach_stubdom(self):
        self.frontend.virt_mode = "hvm"
        self.frontend.features["stubdom-qrexec"] = True
        self.frontend.start()
        ass = make_assignment(self.backend, self.usbdev_ident)
        try:
            self.loop.run_until_complete(
                self.frontend.devices["usb"].attach(ass)
            )
        except qubesusbproxy.core3ext.USBProxyNotInstalled as e:
            self.skipTest(str(e))

        time.sleep(5)
        self.assertEqual(
            self.frontend.run("lsusb -d 1234:1234", wait=True),
            0,
            "Device connection failed",
        )


class TestQubesDB:
    def __init__(self, data):
        self._data = data

    def read(self, key):
        return self._data.get(key, None)

    def list(self, prefix):
        return [key for key in self._data if key.startswith(prefix)]


class TestApp:
    class Domains(dict):
        def __iter__(self):
            return iter(self.values())

    def __init__(self):
        #: jinja2 environment for libvirt XML templates
        self.env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(
                [
                    "templates",
                    "/etc/qubes/templates",
                    "/usr/share/qubes/templates",
                ]
            ),
            undefined=jinja2.StrictUndefined,
            autoescape=True,
        )
        self.domains = TestApp.Domains()
        self.vmm = mock.Mock()


class TestDeviceCollection:
    def __init__(self, backend_vm, devclass):
        self._exposed = []
        self._assigned = []
        self.backend_vm = backend_vm
        self.devclass = devclass

    def get_assigned_devices(self):
        return self._assigned

    def get_exposed_devices(self):
        yield from self._exposed

    __iter__ = get_exposed_devices

    def __getitem__(self, port_id):
        for dev in self._exposed:
            if dev.port_id == port_id:
                return dev
        raise KeyError()


class TestVM(qubes.tests.TestEmitter):
    def __init__(self, qdb, running=True, name="test-vm", **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.klass = "AdminVM" if name == "dom0" else "AppVM"
        self.icon = "red"
        self.untrusted_qdb = TestQubesDB(qdb)
        self.libvirt_domain = mock.Mock()
        self.features = mock.Mock()
        self.features.check_with_template.side_effect = lambda name, default: (
            "4.2" if name == "qubes-agent-version" else None
        )
        self.is_running = lambda: running
        self.log = mock.Mock()
        self.app = TestApp()
        self.devices = {"testclass": TestDeviceCollection(self, "testclass")}

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        if isinstance(other, TestVM):
            return self.name == other.name
        return False

    def __str__(self):
        return self.name


def get_qdb(attachment=None):
    result = {
        "/qubes-usb-devices/1-1/desc": b"1a0a:badd USB-IF Test\x20Device",
        "/qubes-usb-devices/1-1/interfaces": b":ffff00:020600:0a0000:",
        "/qubes-usb-devices/1-1/usb-ver": b"2",
        "/qubes-usb-devices/1-2/desc": b"1a0a:badd USB-IF Test\x20Device\x202",
        "/qubes-usb-devices/1-2/interfaces": b":0acafe:",
        "/qubes-usb-devices/1-2/usb-ver": b"3",
    }
    if attachment:
        result["/qubes-usb-devices/1-1/connected-to"] = attachment.encode()
    return result


class TC_30_USBProxy_core3(qubes.tests.QubesTestCase):
    # noinspection PyAttributeOutsideInit
    def setUp(self):
        super().setUp()
        self.ext = qubesusbproxy.core3ext.USBDeviceExtension()

    @staticmethod
    def added_assign_setup(attachment=None):
        back_vm = TestVM(qdb=get_qdb(attachment), name="sys-usb")
        front = TestVM({}, name="front-vm")
        dom0 = TestVM({}, name="dom0")
        back_vm.app.domains["sys-usb"] = back_vm
        back_vm.app.domains["front-vm"] = front
        back_vm.app.domains[0] = dom0
        back_vm.app.domains["dom0"] = dom0
        front.app = back_vm.app
        dom0.app = back_vm.app

        back_vm.app.vmm.configure_mock(**{"offline_mode": False})
        fire_event_async = mock.Mock()
        front.fire_event_async = fire_event_async

        back_vm.devices["usb"] = TestDeviceCollection(
            backend_vm=back_vm, devclass="usb"
        )
        front.devices["usb"] = TestDeviceCollection(
            backend_vm=front, devclass="usb"
        )
        dom0.devices["usb"] = TestDeviceCollection(
            backend_vm=dom0, devclass="usb"
        )

        return back_vm, front

    def test_010_on_qdb_change_multiple_assignments_including_full(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        full_assig = DeviceAssignment(
            VirtualDevice(exp_dev.port, exp_dev.device_id),
            mode="auto-attach",
            options={"pid": "did"},
        )
        port_assign = DeviceAssignment(
            VirtualDevice(exp_dev.port, "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        front.devices["usb"]._assigned.append(full_assig)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )

        self.ext.attach_and_notify = Mock()
        loop = asyncio.get_event_loop()
        with mock.patch("asyncio.wait"):
            with mock.patch("asyncio.ensure_future"):
                loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"pid": "did"}
        )

    def test_011_on_qdb_change_multiple_assignments_port_vs_dev(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        port_assign = DeviceAssignment(
            VirtualDevice(exp_dev.port, "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )

        self.ext.attach_and_notify = Mock()
        loop = asyncio.get_event_loop()
        with mock.patch("asyncio.wait"):
            with mock.patch("asyncio.ensure_future"):
                loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"pid": "any"}
        )

    def test_012_on_qdb_change_multiple_assignments_dev(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        port_assign = DeviceAssignment(
            VirtualDevice(Port(exp_dev.backend_domain, "1-2", "usb"), "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-2", "usb"))
        )

        self.ext.attach_and_notify = Mock()
        loop = asyncio.get_event_loop()
        with mock.patch("asyncio.wait"):
            with mock.patch("asyncio.ensure_future"):
                loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"any": "did"}
        )

    def test_013_on_qdb_change_two_fronts(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        assmnt = DeviceAssignment(exp_dev, mode="auto-attach")

        front.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._exposed.append(exp_dev)

        resolver_path = "qubes.ext.utils.resolve_conflicts_and_attach"
        with mock.patch(resolver_path, new_callable=Mock) as resolver:
            with mock.patch("asyncio.ensure_future"):
                self.ext.on_qdb_change(back, None, None)
            resolver.assert_called_once_with(
                self.ext, {"1-1": {front: assmnt, back: assmnt}}
            )

    # call_socket_service returns coroutine
    @unittest.mock.patch(
        'qubes.ext.utils.call_socket_service', new_callable=AsyncMock)
    def test_014_failed_confirmation(self, socket):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        assmnt = DeviceAssignment(exp_dev, mode="auto-attach")

        front.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._exposed.append(exp_dev)

        socket.return_value = "allow:nonsense"

        loop = asyncio.get_event_loop()
        self.ext.attach_and_notify = AsyncMock()
        loop.run_until_complete(
            qubes.ext.utils.resolve_conflicts_and_attach(
                self.ext, {"1-1": {front: assmnt, back: assmnt}}
            )
        )
        self.ext.attach_and_notify.assert_not_called()

    # call_socket_service returns coroutine
    @unittest.mock.patch(
        'qubes.ext.utils.call_socket_service', new_callable=AsyncMock)
    def test_015_successful_confirmation(self, socket):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        assmnt = DeviceAssignment(exp_dev, mode="auto-attach")

        front.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._exposed.append(exp_dev)

        socket.return_value = "allow:front-vm"

        loop = asyncio.get_event_loop()
        self.ext.attach_and_notify = AsyncMock()
        loop.run_until_complete(
            qubes.ext.utils.resolve_conflicts_and_attach(
                self.ext, {"1-1": {front: assmnt, back: assmnt}}
            )
        )
        self.ext.attach_and_notify.assert_called_once_with(front, assmnt)

    def test_016_on_qdb_change_ask(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        assmnt = DeviceAssignment(exp_dev, mode="ask-to-attach")

        front.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._exposed.append(exp_dev)

        resolver_path = "qubes.ext.utils.resolve_conflicts_and_attach"
        with mock.patch(resolver_path, new_callable=Mock) as resolver:
            with mock.patch("asyncio.ensure_future"):
                self.ext.on_qdb_change(back, None, None)
            resolver.assert_called_once_with(self.ext, {"1-1": {front: assmnt}})

    def test_020_on_startup_multiple_assignments_including_full(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        full_assig = DeviceAssignment(
            VirtualDevice(exp_dev.port, exp_dev.device_id),
            mode="auto-attach",
            options={"pid": "did"},
        )
        port_assign = DeviceAssignment(
            VirtualDevice(exp_dev.port, "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        front.devices["usb"]._assigned.append(full_assig)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )

        self.ext.attach_and_notify = AsyncMock()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"pid": "did"}
        )

    def test_021_on_startup_multiple_assignments_port_vs_dev(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        port_assign = DeviceAssignment(
            VirtualDevice(exp_dev.port, "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )

        loop = asyncio.get_event_loop()
        self.ext.attach_and_notify = AsyncMock()
        loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"pid": "any"}
        )

    def test_022_on_startup_multiple_assignments_dev(self):
        back, front = self.added_assign_setup()

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        port_assign = DeviceAssignment(
            VirtualDevice(Port(exp_dev.backend_domain, "1-2", "usb"), "*"),
            mode="auto-attach",
            options={"pid": "any"},
        )
        dev_assign = DeviceAssignment(
            VirtualDevice(
                Port(exp_dev.backend_domain, "*", "usb"), exp_dev.device_id
            ),
            mode="auto-attach",
            options={"any": "did"},
        )

        front.devices["usb"]._assigned.append(dev_assign)
        front.devices["usb"]._assigned.append(port_assign)
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        )
        back.devices["usb"]._exposed.append(
            qubesusbproxy.core3ext.USBDevice(Port(back, "1-2", "usb"))
        )

        self.ext.attach_and_notify = AsyncMock()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.assertEqual(
            self.ext.attach_and_notify.call_args[0][1].options, {"any": "did"}
        )

    def test_023_on_startup_already_attached(self):
        back, front = self.added_assign_setup(attachment="sys-usb")

        exp_dev = qubesusbproxy.core3ext.USBDevice(Port(back, "1-1", "usb"))
        assmnt = DeviceAssignment(
            VirtualDevice(exp_dev.port, exp_dev.device_id), mode="auto-attach"
        )

        front.devices["usb"]._assigned.append(assmnt)
        back.devices["usb"]._exposed.append(exp_dev)

        self.ext.attach_and_notify = Mock()
        loop = asyncio.get_event_loop()
        with mock.patch("asyncio.ensure_future"):
            loop.run_until_complete(self.ext.on_domain_start(front, None))
        self.ext.attach_and_notify.assert_not_called()


def list_tests():
    tests = [TC_00_USBProxy]
    if core3:
        tests += [TC_20_USBProxy_core3]
    return tests


def list_unit_tests():
    tests = []
    if core3:
        tests += [TC_30_USBProxy_core3]
    return tests
