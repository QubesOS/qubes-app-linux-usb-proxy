USB proxy based on USBIP and qrexec
===================================

USB device passthrough using USBIP as a protocol, but qrexec as link layer.
See https://github.com/QubesOS/qubes-issues/issues/531 for more details.

Installation
------------

If you get this error: `ERROR: qubes-usb-proxy not installed in the VM` when running `qvm-usb -a`,
you might want to consider installing this repository inside your VM.

To do this, follow these steps:

1. Download a version of this repository from the [releases].
   In this case, we use version v1.0.11. You might want to use a later version.
   ```shell
   wget https://github.com/QubesOS/qubes-app-linux-usb-proxy/archive/v1.0.11.tar.gz
   ```
   **Note:** We use the `.tar.gz` version as the `tar` command is already installed
   whereas `unzip` needs to be installed separately.
   **Note:** If you use this in a Template VM, you need edit your firewall rules to
   allow access for five minutes.
2. Extract the content.
   ```shell
   tar -xf v1.0.11.tar.gz
   ```
3. Go to the directory `qubes-app-linux-usb-proxy-1.0.11`:
   ```shell
   cd qubes-app-linux-usb-proxy-*
   ```
   If you do `ls`, you should see about the same contents as in this folder and this file.
4. Install the `qubes-usb-proxy`.
   ```
   sudo make install-vm
   ```
5. Now, restart or shutdown your VM. If you used a Template VM for this, restart your
   App VM once the Template VM has shut down.
   
[releases]: https://github.com/QubesOS/qubes-app-linux-usb-proxy/releases

Technical details of USBIP
--------------------------

USBIP consists of two parts:

- frontend module (vhci-hcd) - virtual USB controller
- backend module (usbip-host) - USB device driver

Normally (with TCP as link layer), configuration is handled by `usbipd` and
`usbip` tools. Those tools setup TCP connection, then pass socket FD to the
kernel.
For frontend it is done by writing port number, transport socket FD, busid and
speed to `/sys/devices/platform/vhci_hcd/attach`. For backend - by attaching
driver to appropriate device, then writing socket FD to
`/sys/bus/usb/devices/.../usbip_sockfd`.

In case of qrexec, it can also provide a single (local) socket, which can be
used for that purpose. One need to send `SIGUSR1` to `$QREXEC_AGENT_PID` to
switch to that mode (other wise separate sockets are used for data IN (stdin)
and OUT (stdout)) - then stdin (FD 0) can be used for both directions.

Some more info is needed at frontend side (vhci-hcd):

 - port number - can be obtained by parsing
`/sys/devices/platform/vhci_hcd/status` (status codes are defined in
`/usr/include/linux/usbip.h`)
 - remote devid (bus and dev number) and device speed - can be obtained by the
 backend and transfered to the frontend script


Low level description
---------------------

Internally three qrexec services are used:

1. `qubes.USBAttach` - called by dom0 in frontend domain to initiate
   connection. Requires backend domain name and device busid on its stdin
   (separated by space). Service will terminate as soon as connection is
   established.
2. `qubes.USBDetach` - similar to `qubes.USBAttach` but to terminate the
   connection. Parameters on stdin are the same.
3. `qubes.USB` - actual USBIP connection, called by frontend domain to the
   backend domain, with desired busid as 
   [service argument](https://github.com/QubesOS/qubes-issues/issues/1876).


`qubes.USBAttach` service calls `qubes.USB` in the backend, using `usb-import`
script as local process. `usb-import` script is responsible for configuring vhci-hcd,
which includes:

- finding free port
- retrieving (and validating) devid and speed from the backend
- actually attaching the device using FD number 0 (stdin)

It also save state information (port number) for later use by `qubes.USBDetach`.

`qubes.USB` service calls `usb-export` script, which resolve/validate given
device, bind it to the usbip-host driver and send devid and speed to the
frontend (in a single line, space delimited). After that, it hands over stdin
socket (FD 0) to the kernel for USBIP communication. 

Supported argument formats:
 - `VENDORID.PRODUCTID`, where each of them is in 0xHHHH format (four hex digits)
 - `BUSNUM-DEVNUM.PORT`, device name as in `/sys/bus/usb/devices` (important:
   whole device, not a signle interface!)

Low level usage
---------------

First you need to setup qrexec policy to access `qubes.USB+DEVID` calls from
frontend to backend. Then you need to call `qubes.USBAttach` service. Examples
below.

Attach device `2-1` of domain `sys-usb` to domain `work-usb`:

    echo sys-usb 2-1 | qvm-run -p -u root work-usb 'QUBESRPC qubes.USBAttach dom0'

Detach that device:

    echo sys-usb 2-1 | qvm-run -p -u root work-usb 'QUBESRPC qubes.USBDetach dom0'

Alternativelly you can detach the device calling backend domain (USB VM):

    echo 2-1 | qvm-run -p -u root sys-usb 'QUBESRPC qubes.USBDetach dom0'

Using python API it will be:

    frontend_vm.run_service('qubes.USBAttach', input='sys-usb 2-1', user='root')
    frontend_vm.run_service('qubes.USBDetach', input='sys-usb 2-1', user='root')

    backend_vm.run_service('qubes.USBDetach', input='2-1', user='root')
