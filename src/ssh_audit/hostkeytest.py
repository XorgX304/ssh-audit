"""
   The MIT License (MIT)

   Copyright (C) 2017-2020 Joe Testa (jtesta@positronsecurity.com)

   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:

   The above copyright notice and this permission notice shall be included in
   all copies or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
   THE SOFTWARE.
"""
import os

# pylint: disable=unused-import
from typing import Dict, List, Set, Sequence, Tuple, Iterable  # noqa: F401
from typing import Callable, Optional, Union, Any  # noqa: F401

from ssh_audit.kexdh import KexDH, KexGroup1, KexGroup14_SHA1, KexGroup14_SHA256, KexCurve25519_SHA256, KexGroup16_SHA512, KexGroup18_SHA512, KexGroupExchange_SHA1, KexGroupExchange_SHA256, KexNISTP256, KexNISTP384, KexNISTP521
from ssh_audit.protocol import Protocol
from ssh_audit.ssh2_kex import SSH2_Kex
from ssh_audit.ssh2_kexdb import SSH2_KexDB
from ssh_audit.ssh_socket import SSH_Socket


# Obtains host keys, checks their size, and derives their fingerprints.
class HostKeyTest:
    # Tracks the RSA host key types.  As of this writing, testing one in this family yields valid results for the rest.
    RSA_FAMILY = ['ssh-rsa', 'rsa-sha2-256', 'rsa-sha2-512']

    # Dict holding the host key types we should extract & parse.  'cert' is True to denote that a host key type handles certificates (thus requires additional parsing).  'variable_key_len' is True for host key types that can have variable sizes (True only for RSA types, as the rest are of fixed-size).  After the host key type is fully parsed, the key 'parsed' is added with a value of True.
    HOST_KEY_TYPES = {
        'ssh-rsa':      {'cert': False, 'variable_key_len': True},
        'rsa-sha2-256': {'cert': False, 'variable_key_len': True},
        'rsa-sha2-512': {'cert': False, 'variable_key_len': True},

        'ssh-rsa-cert-v01@openssh.com':     {'cert': True, 'variable_key_len': True},

        'ssh-ed25519':                      {'cert': False, 'variable_key_len': False},
        'ssh-ed25519-cert-v01@openssh.com': {'cert': True, 'variable_key_len': False},
    }

    @staticmethod
    def run(s: 'SSH_Socket', server_kex: 'SSH2_Kex') -> None:
        KEX_TO_DHGROUP = {
            'diffie-hellman-group1-sha1': KexGroup1,
            'diffie-hellman-group14-sha1': KexGroup14_SHA1,
            'diffie-hellman-group14-sha256': KexGroup14_SHA256,
            'curve25519-sha256': KexCurve25519_SHA256,
            'curve25519-sha256@libssh.org': KexCurve25519_SHA256,
            'diffie-hellman-group16-sha512': KexGroup16_SHA512,
            'diffie-hellman-group18-sha512': KexGroup18_SHA512,
            'diffie-hellman-group-exchange-sha1': KexGroupExchange_SHA1,
            'diffie-hellman-group-exchange-sha256': KexGroupExchange_SHA256,
            'ecdh-sha2-nistp256': KexNISTP256,
            'ecdh-sha2-nistp384': KexNISTP384,
            'ecdh-sha2-nistp521': KexNISTP521,
            # 'kexguess2@matt.ucc.asn.au': ???
        }

        # Pick the first kex algorithm that the server supports, which we
        # happen to support as well.
        kex_str = None
        kex_group = None
        for server_kex_alg in server_kex.kex_algorithms:
            if server_kex_alg in KEX_TO_DHGROUP:
                kex_str = server_kex_alg
                kex_group = KEX_TO_DHGROUP[kex_str]()
                break

        if kex_str is not None and kex_group is not None:
            HostKeyTest.perform_test(s, server_kex, kex_str, kex_group, HostKeyTest.HOST_KEY_TYPES)

    @staticmethod
    def perform_test(s: 'SSH_Socket', server_kex: 'SSH2_Kex', kex_str: str, kex_group: 'KexDH', host_key_types: Dict[str, Dict[str, bool]]) -> None:
        hostkey_modulus_size = 0
        ca_modulus_size = 0

        # If the connection still exists, close it so we can test
        # using a clean slate (otherwise it may exist in a non-testable
        # state).
        if s.is_connected():
            s.close()

        # For each host key type...
        for host_key_type in host_key_types:
            # Skip those already handled (i.e.: those in the RSA family, as testing one tests them all).
            if 'parsed' in host_key_types[host_key_type] and host_key_types[host_key_type]['parsed']:
                continue

            # If this host key type is supported by the server, we test it.
            if host_key_type in server_kex.key_algorithms:
                cert = host_key_types[host_key_type]['cert']
                variable_key_len = host_key_types[host_key_type]['variable_key_len']

                # If the connection is closed, re-open it and get the kex again.
                if not s.is_connected():
                    err = s.connect()
                    if err is not None:
                        return

                    _, _, err = s.get_banner()
                    if err is not None:
                        s.close()
                        return

                    # Parse the server's initial KEX.
                    packet_type = 0  # pylint: disable=unused-variable
                    packet_type, payload = s.read_packet()
                    SSH2_Kex.parse(payload)

                # Send the server our KEXINIT message, using only our
                # selected kex and host key type.  Send the server's own
                # list of ciphers and MACs back to it (this doesn't
                # matter, really).
                client_kex = SSH2_Kex(os.urandom(16), [kex_str], [host_key_type], server_kex.client, server_kex.server, False, 0)

                s.write_byte(Protocol.MSG_KEXINIT)
                client_kex.write(s)
                s.send_packet()

                # Do the initial DH exchange.  The server responds back
                # with the host key and its length.  Bingo.  We also get back the host key fingerprint.
                kex_group.send_init(s)
                host_key = kex_group.recv_reply(s, variable_key_len)
                if host_key is not None:
                    server_kex.set_host_key(host_key_type, host_key)

                hostkey_modulus_size = kex_group.get_hostkey_size()
                ca_modulus_size = kex_group.get_ca_size()

                # Close the socket, as the connection has
                # been put in a state that later tests can't use.
                s.close()

                # If the host key modulus or CA modulus was successfully parsed, check to see that its a safe size.
                if hostkey_modulus_size > 0 or ca_modulus_size > 0:
                    # Set the hostkey size for all RSA key types since 'ssh-rsa',
                    # 'rsa-sha2-256', etc. are all using the same host key.
                    # Note, however, that this may change in the future.
                    if cert is False and host_key_type in HostKeyTest.RSA_FAMILY:
                        for rsa_type in HostKeyTest.RSA_FAMILY:
                            server_kex.set_rsa_key_size(rsa_type, hostkey_modulus_size)
                    elif cert is True:
                        server_kex.set_rsa_key_size(host_key_type, hostkey_modulus_size, ca_modulus_size)

                    # Keys smaller than 2048 result in a failure.  Update the database accordingly.
                    if (cert is False) and (hostkey_modulus_size < 2048):
                        for rsa_type in HostKeyTest.RSA_FAMILY:
                            alg_list = SSH2_KexDB.ALGORITHMS['key'][rsa_type]
                            alg_list.append(['using small %d-bit modulus' % hostkey_modulus_size])
                    elif (cert is True) and ((hostkey_modulus_size < 2048) or (ca_modulus_size > 0 and ca_modulus_size < 2048)):  # pylint: disable=chained-comparison
                        alg_list = SSH2_KexDB.ALGORITHMS['key'][host_key_type]
                        min_modulus = min(hostkey_modulus_size, ca_modulus_size)
                        min_modulus = min_modulus if min_modulus > 0 else max(hostkey_modulus_size, ca_modulus_size)
                        alg_list.append(['using small %d-bit modulus' % min_modulus])

                # If this host key type is in the RSA family, then mark them all as parsed (since results in one are valid for them all).
                if host_key_type in HostKeyTest.RSA_FAMILY:
                    for rsa_type in HostKeyTest.RSA_FAMILY:
                        host_key_types[rsa_type]['parsed'] = True
                else:
                    host_key_types[host_key_type]['parsed'] = True
