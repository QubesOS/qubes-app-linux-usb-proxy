RPM_SPEC_FILES.vm := rpm_spec/qubes-usb-proxy.spec
RPM_SPEC_FILES.dom0 := rpm_spec/qubes-usb-proxy-dom0.spec

DEBIAN_BUILD_DIRS := debian

RPM_SPEC_FILES = $(RPM_SPEC_FILES.$(PACKAGE_SET))
