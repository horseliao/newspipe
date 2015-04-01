#! /usr/bin/env python
#-*- coding: utf-8 -*-

# pyAggr3g470r - A Web based news aggregator.
# Copyright (C) 2010-2015  Cédric Bonhomme - https://www.cedricbonhomme.org
#
# For more information : https://bitbucket.org/cedricbonhomme/pyaggr3g470r
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

__author__ = "Cedric Bonhomme"
__version__ = "$Revision: 1.7 $"
__date__ = "$Date: 2010/12/07 $"
__revision__ = "$Date: 2015/03/28 $"
__copyright__ = "Copyright (c) Cedric Bonhomme"
__license__ = "AGPLv3"

#
# This file provides functions used for:
# - detection of duplicate articles;
# - import from a JSON file;
# - generation of tags cloud;
# - HTML processing.
#

import re
import glob
import opml
import json
import logging
import datetime
import operator
import urllib
import itertools
import subprocess
import sqlalchemy
try:
    from urlparse import urlparse, parse_qs, urlunparse
except:
    from urllib.parse import urlparse, parse_qs, urlunparse
from bs4 import BeautifulSoup
from datetime import timedelta
from collections import Counter
from contextlib import contextmanager

import conf
from flask import g
from bootstrap import application as app, db
from pyaggr3g470r import controllers
from pyaggr3g470r.models import User, Feed, Article

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = set(['xml', 'opml', 'json'])

def allowed_file(filename):
    """
    Check if the uploaded file is allowed.
    """
    return '.' in filename and \
            filename.rsplit('.', 1)[1] in ALLOWED_EXTENSIONS

@contextmanager
def opened_w_error(filename, mode="r"):
    try:
        f = open(filename, mode)
    except IOError as err:
        yield None, err
    else:
        try:
            yield f, None
        finally:
            f.close()

def fetch(id, feed_id=None):
    """
    Fetch the feeds in a new processus.
    The "asyncio" crawler is launched with the manager.
    """
    cmd = [conf.PYTHON, conf.basedir+'/manager.py', 'fetch_asyncio', str(id),
            str(feed_id)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)

def history(user_id, year=None, month=None):
    """
    Sort articles by year and month.
    """
    articles_counter = Counter()
    articles = controllers.ArticleController(user_id).read()
    if None != year:
        articles = articles.filter(sqlalchemy.extract('year', Article.date) == year)
        if None != month:
            articles = articles.filter(sqlalchemy.extract('month', Article.date) == month)
    for article in articles.all():
        if None != year:
            articles_counter[article.date.month] += 1
        else:
            articles_counter[article.date.year] += 1
    return articles_counter, articles

def import_opml(email, opml_content):
    """
    Import new feeds from an OPML file.
    """
    user = User.query.filter(User.email == email).first()
    try:
        subscriptions = opml.from_string(opml_content)
    except:
        logger.exception("Parsing OPML file failed:")
        raise

    def read(subsubscription, nb=0):
        """
        Parse recursively through the categories and sub-categories.
        """
        for subscription in subsubscription:
            if len(subscription) != 0:
                nb = read(subscription, nb)
            else:
                try:
                    title = subscription.text
                except:
                    title = ""
                try:
                    description = subscription.description
                except:
                    description = ""
                try:
                    link = subscription.xmlUrl
                except:
                    continue
                if None != Feed.query.filter(Feed.user_id == user.id, Feed.link == link).first():
                    continue
                try:
                    site_link = subscription.htmlUrl
                except:
                    site_link = ""
                new_feed = Feed(title=title, description=description,
                                link=link, site_link=site_link,
                                enabled=True)
                user.feeds.append(new_feed)
                nb += 1
        return nb
    nb = read(subscriptions)
    db.session.commit()
    return nb

def import_json(email, json_content):
    """
    Import an account from a JSON file.
    """
    user = User.query.filter(User.email == email).first()
    json_account = json.loads(json_content)
    nb_feeds, nb_articles = 0, 0
    # Create feeds:
    for feed in json_account["result"]:
        if None != Feed.query.filter(Feed.user_id == user.id,
                                    Feed.link == feed["link"]).first():
            continue
        new_feed = Feed(title=feed["title"],
                        description="",
                        link=feed["link"],
                        site_link=feed["site_link"],
                        created_date=datetime.datetime.\
                            fromtimestamp(int(feed["created_date"])),
                        enabled=feed["enabled"])
        user.feeds.append(new_feed)
        nb_feeds += 1
    db.session.commit()
    # Create articles:
    for feed in json_account["result"]:
        user_feed = Feed.query.filter(Feed.user_id == user.id,
                                        Feed.link == feed["link"]).first()
        if None != user_feed:
            for article in feed["articles"]:
                if None == Article.query.filter(Article.user_id == user.id,
                                    Article.feed_id == user_feed.id,
                                    Article.link == article["link"]).first():
                    new_article = Article(link=article["link"],
                                title=article["title"],
                                content=article["content"],
                                readed=article["readed"],
                                like=article["like"], \
                                retrieved_date=datetime.datetime.\
                                    fromtimestamp(int(article["retrieved_date"])),
                                date=datetime.datetime.\
                                    fromtimestamp(int(article["date"])),
                                user_id=user.id,
                                feed_id=user_feed.id)
                    user_feed.articles.append(new_article)
                    nb_articles += 1
    db.session.commit()
    return nb_feeds, nb_articles

def clean_url(url):
    """
    Remove utm_* parameters
    """
    parsed_url = urlparse(url)
    qd = parse_qs(parsed_url.query, keep_blank_values=True)
    filtered = dict((k, v) for k, v in qd.items()
                                        if not k.startswith('utm_'))
    return urlunparse([
        parsed_url.scheme,
        parsed_url.netloc,
        urllib.parse.quote(urllib.parse.unquote(parsed_url.path)),
        parsed_url.params,
        urllib.parse.urlencode(filtered, doseq=True),
        parsed_url.fragment
    ]).rstrip('=')

def open_url(url):
    """
    Open an URL with the proxy and the user-agent
    specified in the configuration file.
    """
    if conf.HTTP_PROXY == "":
        proxy = {}
    else:
        proxy = {"http" : conf.HTTP_PROXY}
    opener = urllib.request.FancyURLopener(proxy)
    try:
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-agent', conf.USER_AGENT)]
        return (True, opener.open(url))
    except urllib.error.HTTPError as e:
        # server couldn't fulfill the request
        error = (url, e.code, \
                        http.server.BaseHTTPRequestHandler.responses[e.code][1])
        return (False, error)
    except urllib.error.URLError as e:
        # failed to reach the server
        if type(e.reason) == str:
            error = (url, e.reason, e.reason)
            #pyaggr3g470r_log.error(url + " " + e.reason)
        else:
            error = (url, e.reason.errno, e.reason.strerror)
        return (False, error)

def clear_string(data):
    """
    Clear a string by removing HTML tags, HTML special caracters
    and consecutive white spaces (more that one).
    """
    p = re.compile('<[^>]+>') # HTML tags
    q = re.compile('\s') # consecutive white spaces
    return p.sub('', q.sub(' ', data))

def load_stop_words():
    """
    Load the stop words and return them in a list.
    """
    stop_words_lists = glob.glob('./pyaggr3g470r/var/stop_words/*.txt')
    stop_words = []

    for stop_wods_list in stop_words_lists:
        with opened_w_error(stop_wods_list, "r") as (stop_wods_file, err):
            if err:
                stop_words = []
            else:
                stop_words += stop_wods_file.read().split(";")
    return stop_words

def top_words(articles, n=10, size=5):
    """
    Return the n most frequent words in a list.
    """
    stop_words = load_stop_words()
    words = Counter()
    wordre = re.compile(r'\b\w{%s,}\b' % size, re.I)
    for article in articles:
        for word in [elem.lower() for elem in
                wordre.findall(clear_string(article.content)) \
                if elem.lower() not in stop_words]:
            words[word] += 1
    return words.most_common(n)

def tag_cloud(tags):
    """
    Generates a tags cloud.
    """
    tags.sort(key=operator.itemgetter(0))
    return '\n'.join([('<font size=%d><a href="/search?query=%s" title="Count: %s">%s</a></font>' % \
                    (min(1 + count * 7 / max([tag[1] for tag in tags]), 7), word, format(count, ',d'), word)) \
                        for (word, count) in tags])

def compare_documents(feed):
    """
    Compare a list of documents by pair.
    Pairs of duplicates are sorted by "retrieved date".
    """
    duplicates = []
    for pair in itertools.combinations(feed.articles, 2):
        date1, date2 = pair[0].date, pair[1].date
        if clear_string(pair[0].title) == clear_string(pair[1].title) and \
                                        (date1 - date2) < timedelta(days = 1):
            if pair[0].retrieved_date < pair[1].retrieved_date:
                duplicates.append((pair[0], pair[1]))
            else:
                duplicates.append((pair[1], pair[0]))
    return duplicates

def search_feed(url):
    """
    Search a feed in a HTML page.
    """
    soup, page = None, None
    try:
        result = open_url(url)
        if result[0] == True:
            page = open_url(url)[1]
        else:
            return None
        soup = BeautifulSoup(page)
    except:
        return None
    feed_links = soup('link', type='application/atom+xml')
    feed_links.extend(soup('link', type='application/rss+xml'))
    for feed_link in feed_links:
        #if url not in feed_link['href']:
            #return urllib.parse.urljoin(url, feed_link['href'])
        return feed_link['href']
    return None

if __name__ == "__main__":
    import_opml("root@pyAggr3g470r.localhost", "./var/feeds_test.opml")
    #import_opml("root@pyAggr3g470r.localhost", "./var/pyAggr3g470r.opml")
