#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2016 Marek Marczykowski-Górecki
#                                   <marmarek@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301,
# USA.
#

from setuptools import setup
import sys

if sys.version_info < (3,):
    # don't install core3 extension
    setup(
        name='qubesusbproxy',
        version=open('version').read().strip(),
        packages=['qubesusbproxy'],
        entry_points={
            'qubes.tests.extra.for_template':
                'usbproxy = qubesusbproxy.tests:list_tests',
        },
    )
else:
    # install tests and core3 extension
    setup(
        name='qubesusbproxy',
        version=open('version').read().strip(),
        packages=['qubesusbproxy'],
        entry_points={
            'qubes.tests.extra.for_template':
                'usbproxy = qubesusbproxy.tests:list_tests',
            'qubes.tests.extra':
                'usbproxy = qubesusbproxy.tests:list_unit_tests',
            'qubes.ext':
                'usbproxy = qubesusbproxy.core3ext:USBDeviceExtension',
            'qubes.devices':
                'usb = qubesusbproxy.core3ext:USBDevice',
        }, install_requires=['lxml']
    )
