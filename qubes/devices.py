#!/usr/bin/python2 -O
# vim: fileencoding=utf-8

#
# The Qubes OS Project, https://www.qubes-os.org/
#
# Copyright (C) 2010-2016  Joanna Rutkowska <joanna@invisiblethingslab.com>
# Copyright (C) 2015-2016  Wojtek Porczyk <woju@invisiblethingslab.com>
# Copyright (C) 2016       Bahtiar `kalkin-` Gadimov <bahtiar@gadimov.de>
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

import re

import qubes.utils


class DeviceCollection(object):
    '''Bag for devices.

    Used as default value for :py:meth:`DeviceManager.__missing__` factory.

    :param vm: VM for which we manage devices
    :param class_: device class
    '''

    def __init__(self, vm, class_):
        self._vm = vm
        self._class = class_
        self._set = set()

        self.devclass = qubes.utils.get_entry_point_one(
            'qubes.devices', self._class)

    def attach(self, device):
        '''Attach (add) device to domain.

        :param DeviceInfo device: device object
        '''

        if device in self.attached():
            raise KeyError(
                'device {!r} of class {} already attached to {!r}'.format(
                    device, self._class, self._vm))
        self._vm.fire_event_pre('device-pre-attach:' + self._class, device)
        self._set.add(device)
        self._vm.fire_event('device-attach:' + self._class, device)


    def detach(self, device):
        '''Detach (remove) device from domain.

        :param DeviceInfo device: device object
        '''

        if device not in self.attached():
            raise KeyError(
                'device {!r} of class {} not attached to {!r}'.format(
                    device, self._class, self._vm))
        self._vm.fire_event_pre('device-pre-detach:' + self._class, device)
        self._set.remove(device)
        self._vm.fire_event('device-detach:' + self._class, device)

    def attached(self, persistent=None, attached=None):
        '''List devices which are (or may be) attached to this vm

        Devices may be attached persistently (so they are included in
        :file:`qubes.xml`) or not. Device can also be in :file:`qubes.xml`,
        but be temporarily detached.

        :param bool persistent: only include devices which are (or are not) \
        attached persistently
        :param bool attached: onlu include devices which are (or are not)
        really attached
        '''
        seen = self._set.copy()

        attached = self._vm.fire_event('device-list-attached:' + self._class,
            persistent=persistent, attached=attached)
        for device in attached:
            device_persistent = device in self._set
            if persistent is not None and device_persistent != persistent:
                continue
            assert device.frontend_domain == self._vm

            yield device

            try:
                seen.remove(device)
            except KeyError:
                pass

        if persistent is False:
            return

        for device in seen:
            assert device.frontend_domain is None
            yield device


class DeviceManager(dict):
    '''Device manager that hold all devices by their classess.

    :param vm: VM for which we manage devices
    '''

    def __init__(self, vm):
        super(DeviceManager, self).__init__()
        self._vm = vm

    def __missing__(self, key):
        self[key] = DeviceCollection(self._vm, key)
        return self[key]


class DeviceInfo(object):
    def __init__(self, backend_domain, ident, description=None,
            frontend_domain=None, **kwargs):
        self.backend_domain = backend_domain
        self.ident = ident
        self.description = description
        self.frontend_domain = frontend_domain
        self.data = kwargs

        if hasattr(self, 'regex'):
            dev_match = self.regex.match(ident)
            if not dev_match:
                raise ValueError('Invalid device identifier: {!r}'.format(
                    ident))

            for group in self.regex.groupindex:
                setattr(self, group, dev_match.group(group))

    def __hash__(self):
        return hash(self.ident)

    def __eq__(self, other):
        return (
            self.backend_domain == other.backend_domain and
            self.ident == other.ident
        )


class PCIDevice(DeviceInfo):
    regex = re.compile(
        r'^(?P<bus>[0-9a-f]+):(?P<device>[0-9a-f]+)\.(?P<function>[0-9a-f]+)$')

    @property
    def libvirt_name(self):
        return 'pci_0000_{}_{}_{}'.format(self.bus, self.device, self.function)


class BlockDevice(object):
    # pylint: disable=too-few-public-methods
    def __init__(self, path, name, script=None, rw=True, domain=None,
                 devtype='disk'):
        assert name, 'Missing device name'
        assert path, 'Missing device path'
        self.path = path
        self.name = name
        self.rw = rw
        self.script = script
        self.domain = domain
        self.devtype = devtype
