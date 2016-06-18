%{!?version: %define version %(cat version)}
%if 0%{?qubes_builder}
%define _builddir %(pwd)
%endif

Name:		qubes-usb-proxy
Version:	%{version}
Release:	1%{?dist}
Summary:	USBIP wrapper to run it over Qubes RPC connection

Group:		System
License:	GPLv2
URL:		https://www.qubes-os.org/
BuildArch:  noarch

%description
USBIP wrapper to run it over Qubes RPC connection


%install
make install-vm DESTDIR=${RPM_BUILD_ROOT}


%files
#%%doc
/usr/lib/qubes/usb-import
/etc/qubes-rpc/qubes.USB
/etc/qubes-rpc/qubes.USBAttach
/etc/qubes-rpc/qubes.USBDetach
/usr/lib/qubes/usb-import
/usr/lib/qubes/usb-export


%changelog

