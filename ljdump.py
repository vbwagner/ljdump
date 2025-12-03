#!/usr/bin/python3
#
# ljdump.py - livejournal archiver
# Greg Hewgill <greg@hewgill.com> https://hewgill.com/
# Version 2.0.0
#
# LICENSE
#
# This software is provided 'as-is', without any express or implied
# warranty.  In no event will the author be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
#    claim that you wrote the original software. If you use this software
#    in a product, an acknowledgment in the product documentation would be
#    appreciated but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
#    misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.
#
# Copyright (c) 2005-2010 Greg Hewgill and contributors

import argparse
import codecs
import os
import time
import pickle
import pprint
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import xml.dom.minidom
import xmlrpc.client
from getpass import getpass
from xml.sax import saxutils

MimeExtensions = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

def flatresponse(response):
    r = {}
    while True:
        name = response.readline().decode()
        if len(name) == 0:
            break
        if name[-1] == '\n':
            name = name[:len(name)-1]
        value = response.readline().decode()
        if value[-1] == '\n':
            value = value[:len(value)-1]
        r[name] = value
    return r

def getljsession(server, username, password):
    """Log in with password and get session cookie."""
    qs = f"mode=sessiongenerate&user={urllib.parse.quote(username)}&auth_method=clear&password={urllib.parse.quote(password)}"
    with urllib.request.urlopen(server+"/interface/flat", qs.encode()) as r:
        response = flatresponse(r)
    return response['ljsession']

def dumpelement(f, name, e):
    f.write(f"<{name}>\n")
    for k in sorted(list(e.keys())):
        if isinstance(e[k], {}.__class__):
            dumpelement(f, k, e[k])
        else:
            # Some post bodies are strings, some are Binary. Probably
            # the latter only occurs when there's non-ASCII in a post.
            if isinstance(e[k], xmlrpc.client.Binary):
                try:
                    s = e[k].data.decode()
                except UnicodeDecodeError:
                    # fall back to Latin-1 for old entries that aren't UTF-8
                    s = e[k].data.decode("cp1252")
            else:
                s = str(e[k])
            f.write(f"<{k}>{saxutils.escape(s)}</{k}>\n")
    f.write(f"</{name}>\n")

def writedump(fn, event):
    with codecs.open(fn, "w", encoding='utf-8') as f:
        f.write("""<?xml version="1.0"?>\n""")
        dumpelement(f, "event", event)

def writelast(journal, lastsync, lastmaxid):
    with open(f"{journal}/.last", "w", encoding='utf-8') as f:
        f.write(f"{lastsync}\n")
        f.write(f"{lastmaxid}\n")

def createxml(doc, name, map):
    e = doc.createElement(name)
    for k in sorted(list(map.keys())):
        me = doc.createElement(k)
        me.appendChild(doc.createTextNode(map[k]))
        e.appendChild(me)
    return e

def gettext(e):
    if len(e) == 0:
        return ""
    return e[0].firstChild.nodeValue

def ljdump(Server, Username, Password, Journal, verbose=True):
    m = re.search("(.*)/interface/xmlrpc", Server)
    if m:
        Server = m.group(1)
    if Username != Journal:
        authas = f"&authas={Journal}"
    else:
        authas = ""

    if verbose:
        print(f"Fetching journal entries for: {Journal}")
    try:
        os.mkdir(Journal)
        print(f"Created subdirectory: {Journal}")
    except:
        pass

    ljsession = getljsession(Server, Username, Password)

    server = xmlrpc.client.ServerProxy(Server+"/interface/xmlrpc")

    def authed(params):
        """Transform API call params to include authorization."""
        return dict(auth_method='clear', username=Username, password=Password, **params)

    newentries = 0
    newcomments = 0
    errors = 0

    lastsync = ""
    lastmaxid = 0
    try:
        with open(f"{Journal}/.last", "r", encoding='utf-8') as f:
            lastsync = f.readline()
            if lastsync[-1] == '\n':
                lastsync = lastsync[:len(lastsync)-1]
            lastmaxid = f.readline()
            if len(lastmaxid) > 0 and lastmaxid[-1] == '\n':
                lastmaxid = lastmaxid[:len(lastmaxid)-1]
            if lastmaxid == "":
                lastmaxid = 0
            else:
                lastmaxid = int(lastmaxid)
    except:
        pass
    origlastsync = lastsync

    r = server.LJ.XMLRPC.login(authed({
        'ver': 1,
        'getpickws': 1,
        'getpickwurls': 1,
    }))
    userpics = dict(list(zip(list(map(str, r['pickws'])), r['pickwurls'])))
    if r['defaultpicurl']:
        userpics['*'] = r['defaultpicurl']

    while True:
        time.sleep(0.2)
        r = server.LJ.XMLRPC.syncitems(authed({
            'ver': 1,
            'lastsync': lastsync,
            'usejournal': Journal,
        }))
        #pprint.pprint(r)
        if len(r['syncitems']) == 0:
            break
        for item in r['syncitems']:
            if item['item'][0] == 'L':
                print(f"Fetching journal entry {item['item']} ({item['action']})")
                try:
                    time.sleep(0.2)
                    e = server.LJ.XMLRPC.getevents(authed({
                        'ver': 1,
                        'selecttype': "one",
                        'itemid': item['item'][2:],
                        'usejournal': Journal,
                    }))
                    if e['events']:
                        writedump(f"{Journal}/{item['item']}", e['events'][0])
                        newentries += 1
                    else:
                        print(f"Unexpected empty item: {item['item']}")
                        errors += 1
                except xmlrpc.client.Fault as x:
                    print(f"Error getting item: {item['item']}")
                    pprint.pprint(x)
                    errors += 1
                    if str(x).find("will be able to continue posting within an hour."):
                        print "Waiting a hour"
                        time.sleep(3600)
                        continue
            lastsync = item['time']
            writelast(Journal, lastsync, lastmaxid)

    # The following code doesn't work because the server rejects our repeated calls.
    # https://www.livejournal.com/doc/server/ljp.csp.xml-rpc.getevents.html
    # contains the statement "You should use the syncitems selecttype in
    # conjuntions [sic] with the syncitems protocol mode", but provides
    # no other explanation about how these two function calls should
    # interact. Therefore we just do the above slow one-at-a-time method.

    #while True:
    #    r = server.LJ.XMLRPC.getevents(authed({
    #        'ver': 1,
    #        'selecttype': "syncitems",
    #        'lastsync': lastsync,
    #    }))
    #    pprint.pprint(r)
    #    if len(r['events']) == 0:
    #        break
    #    for item in r['events']:
    #        writedump(f"{Journal}/L-{item['itemid']}", item)
    #        newentries += 1
    #        lastsync = item['eventtime']

    if verbose:
        print(f"Fetching journal comments for: {Journal}")

    try:
        with open(f"{Journal}/comment.meta", "rb") as f:
            metacache = pickle.load(f)
    except:
        metacache = {}

    try:
        with open(f"{Journal}/user.map", "rb") as f:
            usermap = pickle.load(f)
    except:
        usermap = {}

    maxid = lastmaxid
    while True:
        try:
            time.sleep(0.2)
            with urllib.request.urlopen(urllib.request.Request(Server + f"/export_comments.bml?get=comment_meta&startid={maxid+1}{authas}", headers = {'Cookie': "ljsession="+ljsession})) as r:
                meta = xml.dom.minidom.parse(r)
        except Exception as x:
            print("*** Error fetching comment meta, possibly not community maintainer?")
            print("***", x)
            break
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            metacache[id] = {
                'posterid': c.getAttribute("posterid"),
                'state': c.getAttribute("state"),
            }
            if id > maxid:
                maxid = id
        for u in meta.getElementsByTagName("usermap"):
            usermap[u.getAttribute("id")] = u.getAttribute("user")
        if maxid >= int(meta.getElementsByTagName("maxid")[0].firstChild.nodeValue):
            break

    with open(f"{Journal}/comment.meta", "wb") as f:
        pickle.dump(metacache, f)

    with open(f"{Journal}/user.map", "wb") as f:
        pickle.dump(usermap, f)

    newmaxid = maxid
    maxid = lastmaxid
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(Server + f"/export_comments.bml?get=comment_body&startid={maxid+1}{authas}", headers = {'Cookie': "ljsession="+ljsession})) as r:
                meta = xml.dom.minidom.parse(r)
        except Exception as x:
            print("*** Error fetching comment body, possibly not community maintainer?")
            print("***", x)
            break
        for c in meta.getElementsByTagName("comment"):
            id = int(c.getAttribute("id"))
            jitemid = c.getAttribute("jitemid")
            comment = {
                'id': str(id),
                'parentid': c.getAttribute("parentid"),
                'subject': gettext(c.getElementsByTagName("subject")),
                'date': gettext(c.getElementsByTagName("date")),
                'body': gettext(c.getElementsByTagName("body")),
                'state': metacache[id]['state'],
            }
            if c.getAttribute("posterid") in usermap:
                comment["user"] = usermap[c.getAttribute("posterid")]
            try:
                entry = xml.dom.minidom.parse(f"{Journal}/C-{jitemid}")
            except:
                entry = xml.dom.minidom.getDOMImplementation().createDocument(None, "comments", None)
            found = False
            for d in entry.getElementsByTagName("comment"):
                if int(d.getElementsByTagName("id")[0].firstChild.nodeValue) == id:
                    found = True
                    break
            if found:
                print(f"Warning: downloaded duplicate comment id {id} in jitemid {jitemid}")
            else:
                entry.documentElement.appendChild(createxml(entry, "comment", comment))
                with codecs.open(f"{Journal}/C-{jitemid}", "w", encoding='utf-8') as f:
                    entry.writexml(f)
                newcomments += 1
            if id > maxid:
                maxid = id
        if maxid >= newmaxid:
            break

    lastmaxid = maxid

    writelast(Journal, lastsync, lastmaxid)

    if Username == Journal:
        if verbose:
            print(f"Fetching userpics for: {Username}")
        with open(f"{Username}/userpics.xml", "w", encoding='utf-8') as f:
            print("""<?xml version="1.0"?>""", file=f)
            print("<userpics>", file=f)
            for p in userpics:
                print(f'<userpic keyword="{p}" url="{userpics[p]}" />', file=f)
                with urllib.request.urlopen(userpics[p]) as pic:
                    ext = MimeExtensions.get(pic.info()["Content-Type"], "")
                    picfn = re.sub(r'[*?\\/:<>"|]', "_", p)
                    try:
                        picfn = codecs.utf_8_decode(picfn)[0]
                    except:
                        # for installations where the above utf_8_decode doesn't work
                        picfn = "".join([ord(x) < 128 and x or "_" for x in picfn])
                    with open(f"{Username}/{picfn}{ext}", "wb") as picf:
                        shutil.copyfileobj(pic, picf)
            print("</userpics>", file=f)

    if verbose or (newentries > 0 or newcomments > 0):
        if origlastsync:
            print(f"{newentries} new entries, {newcomments} new comments (since {origlastsync})")
        else:
            print(f"{newentries} new entries, {newcomments} new comments")
    if errors > 0:
        print(f"{errors} errors")

if __name__ == "__main__":
    args = argparse.ArgumentParser(description="Livejournal archive utility")
    args.add_argument("--quiet", "-q", action='store_false', dest='verbose',
                      help="reduce log output")
    args = args.parse_args()
    config_path = os.getenv("LJDUMP_CONFIG_PATH", "ljdump.config")
    if os.access(config_path, os.F_OK):
        config = xml.dom.minidom.parse(config_path)
        server = config.documentElement.getElementsByTagName("server")[0].childNodes[0].data
        username = config.documentElement.getElementsByTagName("username")[0].childNodes[0].data
        password_els = config.documentElement.getElementsByTagName("password")
        if len(password_els) > 0:
            password = password_els[0].childNodes[0].data
        else:
            password = getpass("Password: ")
        journals = [e.childNodes[0].data for e in config.documentElement.getElementsByTagName("journal")]
        if not journals:
            journals = [username]
    else:
        print("ljdump - livejournal archiver")
        print()
        default_server = "https://livejournal.com"
        server = input(f"Alternative server to use (e.g. 'https://www.dreamwidth.org'), or hit return for '{default_server}': ") or default_server
        print()
        print("Enter your Livejournal username and password.")
        print()
        username = input("Username: ")
        password = getpass("Password: ")
        print()
        print("You may back up either your own journal, or a community.")
        print("If you are a community maintainer, you can back up both entries and comments.")
        print("If you are not a maintainer, you can back up only entries.")
        print()
        journal = input(f"Journal to back up (or hit return to back up '{username}'): ")
        print()
        if journal:
            journals = [journal]
        else:
            journals = [username]

    for journal in journals:
        ljdump(server, username, password, journal, args.verbose)
# vim:ts=4 et:	
