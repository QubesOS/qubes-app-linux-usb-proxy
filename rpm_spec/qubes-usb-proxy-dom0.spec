#%%{!?version: %define version %(cat version)}
%if 0%{?qubes_builder}
%define _builddir %(pwd)
%endif

Name:		qubes-usb-proxy-dom0
Version:	0.1
Release:	1%{?dist}
Summary:	USBIP wrapper to run it over Qubes RPC connection - dom0 files

Group:		System
License:	GPLv2
URL:		https://www.qubes-os.org/
BuildArch:  noarch

BuildRequires:	python
Requires:	python

%description
Dom0 files for Qubes USBIP wrapper. This includes Qubes tools integration.
This package also contains tests.

%install
make install-dom0 DESTDIR=${RPM_BUILD_ROOT}


%files
%attr(0664,root,qubes) %config(noreplace) /etc/qubes-rpc/policy/qubes.USB
%{python_sitelib}/qubesusbproxy-*.egg-info/*
%{python_sitelib}/qubesusbproxy


%changelog

