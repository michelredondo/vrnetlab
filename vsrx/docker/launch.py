#!/usr/bin/env python3

import datetime
import logging
import os
import re
import signal
import subprocess
import sys
import uuid

import vrnetlab

STARTUP_CONFIG_FILE = "/config/startup-config.cfg"


def handle_SIGCHLD(signal, frame):
    os.waitpid(-1, os.WNOHANG)


def handle_SIGTERM(signal, frame):
    sys.exit(0)


signal.signal(signal.SIGINT, handle_SIGTERM)
signal.signal(signal.SIGTERM, handle_SIGTERM)
signal.signal(signal.SIGCHLD, handle_SIGCHLD)

TRACE_LEVEL_NUM = 9
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, message, *args, **kws):
    # Yes, logger takes its '*args' as 'args'.
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, message, args, **kws)


logging.Logger.trace = trace


class VSRX_vm(vrnetlab.VM):
    def __init__(self, hostname, username, password, conn_mode):
        for e in os.listdir("/"):
            if re.search(".qcow2$", e):
                disk_image = "/" + e
        super(VSRX_vm, self).__init__(
            username,
            password,
            disk_image=disk_image,
            ram=4096,
            driveif="virtio",
            cpu="SandyBridge,vme=on,ss=on,vmx=on,f16c=on,rdrand=on,hypervisor=on,arat=on,tsc-adjust=on,umip=on,arch-capabilities=on,pdpe1gb=on,skip-l1dfl-vmentry=on,pschange-mc-no=on,bmi1=off,avx2=off,bmi2=off,erms=off,invpcid=off,rdseed=off,adx=off,smap=off,xsaveopt=off,abm=off,svm=on,aes=on",
            smp="2,sockets=1,cores=2,threads=1",
            mgmt_passthrough=False,
        )
        self.nic_type = "virtio-net-pci"
        self.conn_mode = conn_mode
        self.num_nics = 10
        self.hostname = hostname

        with open("init.conf", "r") as file:
            cfg = file.read()

        cfg = cfg.replace("{MGMT_IP_IPV4}", self.mgmt_address_ipv4)
        cfg = cfg.replace("{MGMT_GW_IPV4}", self.mgmt_gw_ipv4)
        cfg = cfg.replace("{MGMT_IP_IPV6}", self.mgmt_address_ipv6)
        cfg = cfg.replace("{MGMT_GW_IPV6}", self.mgmt_gw_ipv6)
        cfg = cfg.replace("{HOSTNAME}", self.hostname)

        with open("init.conf", "w") as file:
            cfg = file.write(cfg)

        self.startup_config()
        
        # generate UUID to attach
        self.qemu_args.extend(["-uuid", str(uuid.uuid4())])
        # mount config disk with startup config (juniper.conf)
        self.qemu_args.extend(
            [
                "-drive",
                "if=ide,index=1,id=config_disk,file=/config.iso,media=cdrom",
            ]
        )

    def startup_config(self):
        """Load additional config provided by user and append initial
        configurations set by vrnetlab."""
        # if startup cfg DNE
        if not os.path.exists(STARTUP_CONFIG_FILE):
            self.logger.trace(f"Startup config file {STARTUP_CONFIG_FILE} is not found")
            # rename init.conf to juniper.conf, this is our startup config
            os.rename("init.conf", "juniper.conf")

        # if startup cfg file is found
        else:
            self.logger.trace(
                f"Startup config file {STARTUP_CONFIG_FILE} found, appending initial configuration"
            )
            # append startup cfg to inital configuration
            append_cfg = f"cat init.conf {STARTUP_CONFIG_FILE} >> juniper.conf"
            subprocess.run(append_cfg, shell=True)

        # generate mountable config disk based on juniper.conf file with base vrnetlab configs
        subprocess.run(
            ["./make-config-iso.sh", "juniper.conf", "config.iso"], check=True
        )

    def bootstrap_spin(self):
        """This function should be called periodically to do work."""

        if self.spins > 300:
            # too many spins with no result ->  give up
            self.stop()
            self.start()
            return

        (ridx, match, res) = self.tn.expect([b"login:"], 1)
        if match:  # got a match!
            if ridx == 0:  # login
                self.logger.info("VM started")
                # close telnet connection
                self.tn.close()
                # startup time?
                startup_time = datetime.datetime.now() - self.start_time
                self.logger.info("Startup complete in: %s" % startup_time)
                # mark as running
                self.running = True
                return

        # no match, if we saw some output from the router it's probably
        # booting, so let's give it some more time
        if res != b"":
            self.logger.trace("OUTPUT: %s" % res.decode())
            # reset spins if we saw some output
            self.spins = 0

        self.spins += 1

        return


class VSRX(vrnetlab.VR):
    def __init__(self, hostname, username, password, conn_mode):
        super(VSRX, self).__init__(username, password)
        self.vms = [VSRX_vm(hostname, username, password, conn_mode)]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "--trace", action="store_true", help="enable trace level logging"
    )
    parser.add_argument("--hostname", default="vr-vsrx", help="SRX hostname")
    parser.add_argument("--username", default="vrnetlab", help="Username")
    parser.add_argument("--password", default="VR-netlab9", help="Password")
    parser.add_argument(
        "--connection-mode", default="tc", help="Connection mode to use in the datapath"
    )
    args = parser.parse_args()

    LOG_FORMAT = "%(asctime)s: %(module)-10s %(levelname)-8s %(message)s"
    logging.basicConfig(format=LOG_FORMAT)
    logger = logging.getLogger()

    logger.setLevel(logging.DEBUG)
    if args.trace:
        logger.setLevel(1)

    vr = VSRX(
        args.hostname,
        args.username,
        args.password,
        conn_mode=args.connection_mode,
    )
    vr.start()
