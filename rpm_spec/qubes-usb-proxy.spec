#%%{!?version: %define version %(cat version)}
%if 0%{?qubes_builder}
%define _builddir %(pwd)
%endif

Name:		qubes-usb-proxy
Version:	0.1
Release:	1%{?dist}
Summary:	USBIP wrapper to run it over Qubes RPC connection

Group:		System
License:	GPLv2
URL:		https://www.qubes-os.org/
BuildArch:  noarch

%description
USBIP wrapper to run it over Qubes RPC connection


%install
%make_install


%files
#%%doc
/usr/lib/qubes/usb-import
/etc/qubes-rpc/qubes.USB
/etc/qubes-rpc/qubes.USBAttach
/etc/qubes-rpc/qubes.USBDetach
/usr/lib/qubes/usb-import
/usr/lib/qubes/usb-export


%changelog

