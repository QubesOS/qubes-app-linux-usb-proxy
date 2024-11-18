install-vm:
	install -d $(DESTDIR)/etc/qubes-rpc
	install -p qubes-rpc/qubes.USBAttach $(DESTDIR)/etc/qubes-rpc
	install -p qubes-rpc/qubes.USBDetach $(DESTDIR)/etc/qubes-rpc
	install -p qubes-rpc/qubes.USB $(DESTDIR)/etc/qubes-rpc
	install -d $(DESTDIR)/usr/lib/qubes
	install -p src/usb-* $(DESTDIR)/usr/lib/qubes
	install -d $(DESTDIR)/usr/lib/udev/rules.d
	install -p src/*.rules $(DESTDIR)/usr/lib/udev/rules.d
	install -d $(DESTDIR)/etc/qubes/suspend-pre.d
	ln -s ../../../usr/lib/qubes/usb-detach-all \
		$(DESTDIR)/etc/qubes/suspend-pre.d/usb-detach-all.sh

install-dom0:
	python3 setup.py install -O1 --root $(DESTDIR)
	install -D -m 0664 qubes-rpc/qubes.USB.policy $(DESTDIR)/etc/qubes-rpc/policy/qubes.USB

