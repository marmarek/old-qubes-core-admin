#!/usr/bin/python2
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2010  Joanna Rutkowska <joanna@invisiblethingslab.com>
# Copyright (C) 2013  Marek Marczykowski <marmarek@invisiblethingslab.com>
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
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
#
#

from datetime import datetime

from qubes.qubes import QubesNetVm,register_qubes_vm_class,xs,dry_run
from qubes.qubes import QubesVmCollection,QubesException

yum_proxy_ip = '10.137.255.254'
yum_proxy_port = '8082'

class QubesProxyVm(QubesNetVm):
    """
    A class that represents a ProxyVM, ex FirewallVM. A child of QubesNetVM.
    """

    def get_attrs_config(self):
        attrs_config = super(QubesProxyVm, self).get_attrs_config()
        attrs_config['uses_default_netvm']['func'] = lambda x: False
        # Save netvm prop again
        attrs_config['netvm']['save'] = \
            lambda: str(self.netvm.qid) if self.netvm is not None else "none"

        return attrs_config

    def __init__(self, **kwargs):
        super(QubesProxyVm, self).__init__(**kwargs)
        self.rules_applied = None

    @property
    def type(self):
        return "ProxyVM"

    def is_proxyvm(self):
        return True

    def _set_netvm(self, new_netvm):
        old_netvm = self.netvm
        super(QubesProxyVm, self)._set_netvm(new_netvm)
        if self.netvm is not None:
            self.netvm.add_external_ip_permission(self.get_xid())
        self.write_netvm_domid_entry()
        if old_netvm is not None:
            old_netvm.remove_external_ip_permission(self.get_xid())

    def post_vm_net_attach(self, vm):
        """ Called after some VM net-attached to this ProxyVm """

        self.write_iptables_xenstore_entry()

    def post_vm_net_detach(self, vm):
        """ Called after some VM net-detached from this ProxyVm """

        self.write_iptables_xenstore_entry()

    def start(self, **kwargs):
        if dry_run:
            return
        retcode = super(QubesProxyVm, self).start(**kwargs)
        if self.netvm is not None:
            self.netvm.add_external_ip_permission(self.get_xid())
        self.write_netvm_domid_entry()
        return retcode

    def force_shutdown(self, **kwargs):
        if dry_run:
            return
        if self.netvm is not None:
            self.netvm.remove_external_ip_permission(kwargs['xid'] if 'xid' in kwargs else self.get_xid())
        super(QubesProxyVm, self).force_shutdown(**kwargs)

    def create_xenstore_entries(self, xid = None):
        if dry_run:
            return

        if xid is None:
            xid = self.xid


        super(QubesProxyVm, self).create_xenstore_entries(xid)
        xs.write('', "/local/domain/{0}/qubes-iptables-error".format(xid), '')
        xs.set_permissions('', "/local/domain/{0}/qubes-iptables-error".format(xid),
                [{ 'dom': xid, 'write': True }])
        self.write_iptables_xenstore_entry()

    def write_netvm_domid_entry(self, xid = -1):
        if not self.is_running():
            return

        if xid < 0:
            xid = self.get_xid()

        if self.netvm is None:
            xs.write('', "/local/domain/{0}/qubes-netvm-domid".format(xid), '')
        else:
            xs.write('', "/local/domain/{0}/qubes-netvm-domid".format(xid),
                    "{0}".format(self.netvm.get_xid()))

    def write_iptables_xenstore_entry(self):
        xs.rm('', "/local/domain/{0}/qubes-iptables-domainrules".format(self.get_xid()))
        iptables =  "# Generated by Qubes Core on {0}\n".format(datetime.now().ctime())
        iptables += "*filter\n"
        iptables += ":INPUT DROP [0:0]\n"
        iptables += ":FORWARD DROP [0:0]\n"
        iptables += ":OUTPUT ACCEPT [0:0]\n"

        # Strict INPUT rules
        iptables += "-A INPUT -i vif+ -p udp -m udp --dport 68 -j DROP\n"
        iptables += "-A INPUT -m state --state RELATED,ESTABLISHED -j ACCEPT\n"
        iptables += "-A INPUT -p icmp -j ACCEPT\n"
        iptables += "-A INPUT -i lo -j ACCEPT\n"
        iptables += "-A INPUT -j REJECT --reject-with icmp-host-prohibited\n"

        iptables += "-A FORWARD -m state --state RELATED,ESTABLISHED -j ACCEPT\n"
        # Allow dom0 networking
        iptables += "-A FORWARD -i vif0.0 -j ACCEPT\n"
        # Deny inter-VMs networking
        iptables += "-A FORWARD -i vif+ -o vif+ -j DROP\n"
        iptables += "COMMIT\n"
        xs.write('', "/local/domain/{0}/qubes-iptables-header".format(self.get_xid()), iptables)

        vms = [vm for vm in self.connected_vms.values()]
        for vm in vms:
            iptables="*filter\n"
            conf = vm.get_firewall_conf()

            xid = vm.get_xid()
            if xid < 0: # VM not active ATM
                continue

            ip = vm.ip
            if ip is None:
                continue

            # Anti-spoof rules are added by vif-script (vif-route-qubes), here we trust IP address

            accept_action = "ACCEPT"
            reject_action = "REJECT --reject-with icmp-host-prohibited"

            if conf["allow"]:
                default_action = accept_action
                rules_action = reject_action
            else:
                default_action = reject_action
                rules_action = accept_action

            for rule in conf["rules"]:
                iptables += "-A FORWARD -s {0} -d {1}".format(ip, rule["address"])
                if rule["netmask"] != 32:
                    iptables += "/{0}".format(rule["netmask"])

                if rule["proto"] is not None and rule["proto"] != "any":
                    iptables += " -p {0}".format(rule["proto"])
                    if rule["portBegin"] is not None and rule["portBegin"] > 0:
                        iptables += " --dport {0}".format(rule["portBegin"])
                        if rule["portEnd"] is not None and rule["portEnd"] > rule["portBegin"]:
                            iptables += ":{0}".format(rule["portEnd"])

                iptables += " -j {0}\n".format(rules_action)

            if conf["allowDns"] and self.netvm is not None:
                # PREROUTING does DNAT to NetVM DNSes, so we need self.netvm.
                # properties
                iptables += "-A FORWARD -s {0} -p udp -d {1} --dport 53 -j " \
                            "ACCEPT\n".format(ip,self.netvm.gateway)
                iptables += "-A FORWARD -s {0} -p udp -d {1} --dport 53 -j " \
                            "ACCEPT\n".format(ip,self.netvm.secondary_dns)
                iptables += "-A FORWARD -s {0} -p tcp -d {1} --dport 53 -j " \
                            "ACCEPT\n".format(ip,self.netvm.gateway)
                iptables += "-A FORWARD -s {0} -p tcp -d {1} --dport 53 -j " \
                            "ACCEPT\n".format(ip,self.netvm.secondary_dns)
            if conf["allowIcmp"]:
                iptables += "-A FORWARD -s {0} -p icmp -j ACCEPT\n".format(ip)
            if conf["allowYumProxy"]:
                iptables += "-A FORWARD -s {0} -p tcp -d {1} --dport {2} -j ACCEPT\n".format(ip, yum_proxy_ip, yum_proxy_port)
            else:
                iptables += "-A FORWARD -s {0} -p tcp -d {1} --dport {2} -j DROP\n".format(ip, yum_proxy_ip, yum_proxy_port)

            iptables += "-A FORWARD -s {0} -j {1}\n".format(ip, default_action)
            iptables += "COMMIT\n"
            xs.write('', "/local/domain/"+str(self.get_xid())+"/qubes-iptables-domainrules/"+str(xid), iptables)
        # no need for ending -A FORWARD -j DROP, cause default action is DROP

        self.write_netvm_domid_entry()

        self.rules_applied = None
        xs.write('', "/local/domain/{0}/qubes-iptables".format(self.get_xid()), 'reload')

register_qubes_vm_class(QubesProxyVm)
