#!/usr/bin/bash

check() {
    return 0
}

depends() {
    echo systemd
}

install() {
    inst_simple "$moddir/sysroot-etc.mount"
    inst_simple "$moddir/sysroot-var.mount"
    inst_symlink /usr/lib/systemd/system/sysroot-etc.mount \
        /usr/lib/systemd/system/initrd-root-fs.target.wants/sysroot-etc.mount
    inst_symlink /usr/lib/systemd/system/sysroot-var.mount \
        /usr/lib/systemd/system/initrd-root-fs.target.wants/sysroot-var.mount
}
