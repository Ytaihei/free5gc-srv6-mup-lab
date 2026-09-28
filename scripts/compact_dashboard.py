"""Owned user-service SSH tunnel; never opens an unauthenticated wildcard port."""
import ipaddress
import json
import socket
import subprocess
import time
import urllib.error
import urllib.request

from compact_vm import output, run, write

REMOTE_SOCKET = '/opt/srv6-mup-compact/.lab/runtime/dashboard-socket/http.sock'


class Dashboard:
    def __init__(self, vm):
        self.vm = vm
        self.unit = vm.vm['name'] + '-dashboard.service'
        self.record = vm.state / 'dashboard.json'

    def owned(self):
        owner = self.vm.owned()
        expected = 'SRv6 MUP dashboard ' + owner['domain_uuid']
        properties = output(['systemctl', '--user', 'show', self.unit,
                             '--property=LoadState,Description'])
        data = dict(line.split('=', 1) for line in properties.splitlines() if '=' in line)
        loaded = data.get('LoadState') != 'not-found'
        if loaded and data.get('Description') != expected:
            raise ValueError('dashboard service ownership mismatch; existing service is unchanged')
        return expected, loaded

    def stop(self):
        _, loaded = self.owned()
        if loaded:
            run(['systemctl', '--user', 'stop', self.unit])
            subprocess.run(['systemctl', '--user', 'reset-failed', self.unit],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def bindings(self, tailnet):
        addresses = ['127.0.0.1']
        if tailnet:
            address = ipaddress.ip_address(output(['tailscale', 'ip', '-4']))
            if address.version != 4 or address not in ipaddress.ip_network('100.64.0.0/10'):
                raise ValueError('expected a Tailscale IPv4 address; no public/wildcard binding is allowed')
            addresses.append(str(address))
        return addresses

    def start(self, tailnet=None):
        description, _ = self.owned()
        if tailnet is None:
            tailnet = json.loads(self.record.read_text()).get('tailnet', False) if self.record.exists() else False
        addresses = self.bindings(tailnet)
        port = self.vm.config['dashboard_port']
        self.stop()
        for address in addresses:
            with socket.socket() as listener:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    listener.bind((address, port))
                except OSError as error:
                    raise ValueError(f'dashboard address unavailable: {address}:{port}; {error}') from None
        ssh = self.vm.ssh_args()
        forwards = [item for address in addresses for item in ('-L', f'{address}:{port}:{REMOTE_SOCKET}')]
        command = [*ssh[:-1], '-NT', '-o', 'ExitOnForwardFailure=yes',
                   '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', *forwards, ssh[-1]]
        run(['systemd-run', '--user', '--unit', self.unit, '--collect',
             '--property=Description=' + description, '--property=Restart=on-failure',
             '--property=RestartSec=3', '--', *command])
        # Persist only an explicit scope selection; failed startup must not
        # silently authorize wider exposure on a subsequent up.
        for _ in range(20):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                    if response.status == 204:
                        write(self.record, json.dumps({'tailnet': bool(tailnet)}))
                        print('\n'.join('Dashboard: http://' + address + ':' + str(port) for address in addresses))
                        return
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(1)
        self.stop()
        raise ValueError('dashboard tunnel/snapshot did not become ready; VM retained for diagnosis')

    def status(self):
        self.owned()
        print(output(['systemctl', '--user', 'show', self.unit, '--property=ActiveState,SubState']))
