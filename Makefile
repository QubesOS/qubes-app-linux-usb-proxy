install-vm:
	install -d $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBAttach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USBDetach $(DESTDIR)/etc/qubes-rpc
	install qubes-rpc/qubes.USB $(DESTDIR)/etc/qubes-rpc
	install -d $(DESTDIR)/etc/qubes/rpc-config
	install -T -m 0644 qubes-rpc/qubes.USB.config \
		$(DESTDIR)/etc/qubes/rpc-config/qubes.USB
	install -d $(DESTDIR)/usr/lib/qubes
	install src/usb-* $(DESTDIR)/usr/lib/qubes
	install -d $(DESTDIR)/usr/lib/udev/rules.d
	install -m 644 src/*.rules $(DESTDIR)/usr/lib/udev/rules.d
	install -d $(DESTDIR)/etc/qubes/suspend-pre.d
	ln -s ../../../usr/lib/qubes/usb-detach-all \
		$(DESTDIR)/etc/qubes/suspend-pre.d/usb-detach-all.sh

install-dom0:
	python3 setup.py install -O1 --root $(DESTDIR)

