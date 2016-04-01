install-vm:
	install -d $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBAttach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBDetach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USB $(DESTDIR)/etc/qubes-rpc
	install -d $(DESTDIR)/usr/lib/qubes
	install src/usb-* $(DESTDIR)/usr/lib/qubes

install-dom0:
	python setup.py install -O1 --root $(DESTDIR)
	install -D -m 0664 qubes-rpc/qubes.USB.policy $(DESTDIR)/etc/qubes-rpc/policy/qubes.USB

