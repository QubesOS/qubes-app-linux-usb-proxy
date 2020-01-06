install-vm:
	install -d $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBAttach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBDetach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USB $(DESTDIR)/etc/qubes-rpc
	install -d $(DESTDIR)/usr/lib/qubes
	install src/usb-* $(DESTDIR)/usr/lib/qubes

PYTHON2_SITELIB ?= $(shell python2 -c "from distutils.sysconfig import get_python_lib; print get_python_lib(0)")

install-dom0-py2:
	python2 setup.py install -O1 --root $(DESTDIR)
	rm -f $(DESTDIR)$(PYTHON2_SITELIB)/qubesusbproxy/core3ext.py

install-dom0:
	python3 setup.py install -O1 --root $(DESTDIR)
	install -D -m 0664 qubes-rpc/qubes.USB.policy $(DESTDIR)/etc/qubes-rpc/policy/qubes.USB

