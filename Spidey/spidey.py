import sys, os, requests, string, sqlite3, urllib3, re, hashlib, certifi
from bs4 import BeautifulSoup
from nltk.stem import PorterStemmer
from collections import deque, Counter
from urllib.parse import urlparse, urlunparse, urljoin, urlencode, quote, parse_qs
from spideyTest import outputDatabase
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

# creating a web scraper with selenium, beautifulsoup, and sqlite to get X pages from the given root url into a database setup for later searching

debug = True

if(debug):
    os.chdir('Spidey')

# initializations
visited = set()
bfsQueue = deque()

# detect what operating system is being used, and set the path to the chromedriver accordingly
if sys.platform == 'win32':
    driverPath = './web_Drivers/chromedriver_win32/chromedriver.exe'
# if using macOS, we need to determine if arm or not
elif sys.platform == 'darwin':
    machine = platform.machine()
    if "arm" in machine.lower():
        driverPath = './web_Drivers/chromedriver_mac_arm64/chromedriver'
    driverPath = '.web_Drivers/chromedriver_mac64/chromedriver'
elif sys.platform == 'linux':
    driverPath = './web_Drivers/chromedriver_linux64/chromedriver'
else:
    raise ValueError("Unsupported OS")

# setting chrome options
options = Options()
if(not debug):
   options.add_argument('--headless')
   options.add_argument('--no-sandbox')
   options.add_argument('--disable-dev-shm-usage')
   options.add_argument('--disable-gpu')
service = Service(driverPath)
driver = webdriver.Chrome(service=service, options=options)

# stopword list, imported from a .txt file
stopwords = []
with open('stopwords.txt', 'r') as f:
    for line in f:
        stopwords.append(line.strip())

# remove to the database file if it already exists
try:
    os.remove('spidey.sqlite')
except OSError:
    pass

# adding an sqlite3 database
conn = sqlite3.connect('spidey.sqlite')
cur = conn.cursor()

# creating the page table
cur.execute('''CREATE TABLE Page
             (page_id INTEGER PRIMARY KEY,
              url TEXT,
              title TEXT,
              content TEXT,
              raw_html TEXT,
              last_modified TEXT,
              size INTEGER,
              parent_page_id INTEGER,
              hash TEXT
              )''')

# creating a parent link table for parent links
cur.execute('''CREATE TABLE ParentLink
                (link_id INTEGER PRIMARY KEY,
                page_id INTEGER,
                parent_page_id INTEGER,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(parent_page_id) REFERENCES Page(page_id),
                UNIQUE(page_id, parent_page_id) ON CONFLICT IGNORE
                )''')

# creating a child link table for child links
cur.execute('''CREATE TABLE ChildLink
                (link_id INTEGER PRIMARY KEY,
                page_id INTEGER,
                child_page_id INTEGER,
                child_url TEXT,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(child_page_id) REFERENCES Page(page_id),
                UNIQUE(page_id, child_url) ON CONFLICT IGNORE
                )''')

# creating a term table for keywords
cur.execute('''CREATE TABLE Term
                (term_id INTEGER PRIMARY KEY,
                term TEXT)''')

# creating a term frequency table for keywords (we aren't worried about frequency in titles)
cur.execute('''CREATE TABLE TermFrequency
                (page_id INTEGER,
                term_id INTEGER,
                frequency INTEGER,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(term_id) REFERENCES Term(term_id)
                )''')

# creating a term position table for titles
cur.execute('''CREATE TABLE TitleTermPosition
                (page_id INTEGER,
                term_id INTEGER,
                position_list TEXT,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(term_id) REFERENCES Term(term_id)
                )''')

# creating a term position table for content
cur.execute('''CREATE TABLE ContentTermPosition
                (page_id INTEGER,
                term_id INTEGER,
                position_list TEXT,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(term_id) REFERENCES Term(term_id)
                )''')

# creating an index table for the titles
cur.execute('''CREATE TABLE TitleIndex
                (term_id INTEGER,
                page_id INTEGER,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(term_id) REFERENCES Term(term_id)
                )''')

# creating an index table for the content
cur.execute('''CREATE TABLE ContentIndex
                (term_id INTEGER,
                page_id INTEGER,
                Foreign Key(page_id) REFERENCES Page(page_id),
                Foreign Key(term_id) REFERENCES Term(term_id)
                )''')

conn.commit()

# function to hash pages for later comparison (Reserved for page to page in database comparison in the future, like for page updates)
def hashPage(soup):
    # Remove unwanted elements
    for element in soup(["script", "style", "meta"]):
        element.decompose()
    # Extract the text content of the page
    page_content = soup.get_text()

    page_content = ' '.join(page_content.split())
    # hash the raw html
    return hashlib.sha256(page_content.encode('utf-8')).hexdigest()

# function to canonicalize urls
def canonicalize(url):
    # Canonicalizes a URL by performing the following operations:
    # 1. Normalizes the scheme and hostname to lower case.
    # 2. Removes the default port for the scheme (e.g. port 80 for HTTP).
    # 3. Removes any trailing slashes from the path.
    # 4. Removes any URL fragments.
    # 5. Removes any query parameters that are known not to affect the content of the page.
    # 6. Decodes any percent-encoded characters in the URL.
    # 7. Removes duplicate slashes from the path.
    # 8. Sorts the query parameters by name.
    parsed_url = urlparse(url)
    # Normalize scheme and hostname to lower case
    parsed_url = parsed_url._replace(scheme=parsed_url.scheme.lower())
    parsed_url = parsed_url._replace(netloc=parsed_url.netloc.lower())
    # Remove default ports (these are the most common ones)
    default_ports = {
        'http': 80,
        'https': 443,
        'ftp': 21,
        'ftps': 990,
        'ssh': 22,
        'telnet': 23,
        'smtp': 25,
        'pop3': 110,
        'imap': 143,
        'ldap': 389,
        'ldaps': 636,
    }
    if parsed_url.port == default_ports.get(parsed_url.scheme):
        parsed_url = parsed_url._replace(netloc=parsed_url.hostname)
        parsed_url = parsed_url._replace(port=None)
    # Remove trailing slash from path
    if parsed_url.path.endswith('/') and len(parsed_url.path) > 1:
        parsed_url = parsed_url._replace(path=parsed_url.path.rstrip('/'))
    # Remove URL fragments
    parsed_url = parsed_url._replace(fragment='')
    # Remove query parameters that do not affect page content (these ones should just be used for tracking)
    query_params = []
    for param, value in parse_qs(parsed_url.query, keep_blank_values=True).items():
        if param.lower() in ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'ref']:
            continue
        for v in value:
            query_params.append((param, v))
    if query_params:
        sorted_params = sorted(query_params, key=lambda x: x[0])
        encoded_params = []
        for param, value in sorted_params:
            encoded_params.append((quote(param, safe=''), quote(value, safe='')))
        parsed_url = parsed_url._replace(query=urlencode(encoded_params))
    else:
        parsed_url = parsed_url._replace(query='')
    # Decode percent-encoded characters
    parsed_url = parsed_url._replace(path=quote(parsed_url.path, safe='/'))
    # Remove duplicate slashes from path
    parsed_url = parsed_url._replace(path='/'.join(filter(None, parsed_url.path.split('/'))))
    return urlunparse(parsed_url)

# function to try and get the page, skipping if it fails due to verification or timeout, exits the program if we've run out of links early
def getPage(curUrl, bfsQueue):
    try:
        # get the page with selenium, with a 10 second timeout
        driver.get(curUrl)
        return
    except Exception as e:
        # if there's nothing in the queue, except the code and end the program
        if len(bfsQueue) == 0:
            print("No more links to visit, as the last one has excepted. Exiting...")
            exit()
        else:
            # if there is something else in the queue, move on to that
            nextLink = bfsQueue.popleft()
            while nextLink is None or nextLink in visited:
                if len(bfsQueue) == 0:
                    print("No more links to visit, as the last one has excepted. Exiting...")
                    exit()
                nextLink = bfsQueue.popleft()
            return getPage(nextLink, bfsQueue)

# from each page, we need to get the page title, page url, last modification date, size of page (in characters)
# and the first 10 links on the page, as well as top 10 keywords along with their frequency

# the function to recursively scrape the pages
def scrape(curUrl, targetVisited, parentID, bfsQueue):

    curUrl = canonicalize(curUrl)

    # base case
    if curUrl in visited:
        # if the curUrl has already been visited, the parentURL should be added to the parent table for this pageID, as found from the curUrl
        visitPageID = cur.execute('SELECT page_id FROM Page WHERE url=?', (curUrl,)).fetchone()[0]
        cur.execute('INSERT OR IGNORE INTO ParentLink (page_id, parent_page_id) VALUES (?, ?)', (visitPageID, parentID))
        return
    elif len(visited) >= targetVisited:
        return
    else:
        # get the page
        getPage(curUrl, bfsQueue)
        # parse page
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        if soup.title is not None and soup.title.string.strip() != "":
            title = soup.title.string
        else:
            title = "No Title Given"
        rawHTML = driver.page_source
        hash = hashPage(soup)
        
        # get last modified date by 
        lastModified = driver.execute_script("return document.lastModified")
        if lastModified is None:
            lastModified = driver.execute_script("return document.date")
            if lastModified is None:
                lastModified = "Unkown"

        # get the size of the page by getting the length of the raw html
        size = len(rawHTML)

        # get first 100 links (we have some limiters to make sure we don't get too many links)
        links = []
        for link in soup.find_all('a', limit=200):
            href = link.get('href')
            if href is not None and href.startswith('http'):
                links.append(href)
            if len(links) >= 100:
                break

        text = soup.get_text()

        # Tokenize document content and title
        titleTokens = re.findall(r'\b\w+\b', title.lower())
        contentTokens = re.findall(r'\b\w+\b', text.lower())
        
        # remove stopwords from both title and content
        titleTokens = [token for token in titleTokens if token not in stopwords]
        contentTokens = [token for token in contentTokens if token not in stopwords]

        # stem with porter's
        ps = PorterStemmer()
        titleStems = [ps.stem(token) for token in titleTokens]
        contentStems = [ps.stem(token) for token in contentTokens]

        # count frequency of each word, making a list of tuples
        contentFreq = Counter(contentStems).most_common()
        titleFreq = Counter(titleStems).most_common()

        # inserting the page into the Page table
        cur.execute('''INSERT INTO Page (url, title, content, raw_html, last_modified, size, parent_page_id, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', (curUrl, title, text, rawHTML, lastModified, size, parentID, hash))
        pageID = cur.lastrowid

        # inserting the child links into the ChildLink table (parent links are handled when the child is visited)
        for link in links:
            cur.execute('''INSERT INTO ChildLink (page_id, child_page_id, child_url) VALUES (?, NULL, ?)''', (pageID, link))

        # updating the child link table with the parentID when we are working on the child
        # we find child links with matching parentID as page_id and child_url as curUrl
        # we then update the child_page_id to be the pageID of the current page
        # this is done for all child links with the same parentID and curUrl
        cur.execute('''UPDATE ChildLink SET child_page_id = ? WHERE page_id = ? AND child_url = ?''', (pageID, parentID, curUrl))

        if(parentID is not None):
            cur.execute('''INSERT INTO ParentLink (page_id, parent_page_id) VALUES (?, ?)''', (pageID, parentID))

        # inserting into the term table, if the term is already in the table, it will be skipped
        for term in set(titleStems + contentStems):
            cur.execute('''INSERT OR IGNORE INTO Term (term) VALUES (?)''', (term,))

        # inserting into the term frequency table
        for stem, freq in contentFreq:
            termID = cur.execute('''SELECT term_id FROM Term WHERE term = ?''', (stem,)).fetchone()[0]
            cur.execute("INSERT INTO TermFrequency (page_id, term_id, frequency) VALUES (?, ?, ?)", (pageID, termID, freq))

        # inserting into the ContentTermPosition table
        for stem, freq in contentFreq:
            termID = cur.execute('''SELECT term_id FROM Term WHERE term = ?''', (stem,)).fetchone()[0]
            positions = [i for i, t in enumerate(contentStems) if t == stem]
            positionsList = ','.join(str(pos) for pos in positions) # the list in the database is a string of comma separated integers
            cur.execute("INSERT INTO ContentTermPosition (page_id, term_id, position_list) VALUES (?, ?, ?)", (pageID, termID, positionsList))

        # inserting into the TitleTermPosition table
        for stem, freq in titleFreq:
            termID = cur.execute('''SELECT term_id FROM Term WHERE term = ?''', (stem,)).fetchone()[0]
            positions = [i for i, t in enumerate(titleStems) if t == stem]
            positionsList = ','.join(str(pos) for pos in positions) # the list in the database is a string of comma separated integers
            cur.execute("INSERT INTO TitleTermPosition (page_id, term_id, position_list) VALUES (?, ?, ?)", (pageID, termID, positionsList))
        
        # inserting into the ContentIndex table
        for stem, freq in contentFreq:
            termID = cur.execute('''SELECT term_id FROM Term WHERE term = ?''', (stem,)).fetchone()[0]
            cur.execute("INSERT INTO ContentIndex (term_id, page_id) VALUES (?, ?)", (termID, pageID))
        
        # inserting into the TitleIndex table
        for stem, freq in titleFreq:
            termID = cur.execute('''SELECT term_id FROM Term WHERE term = ?''', (stem,)).fetchone()[0]
            cur.execute("INSERT INTO TitleIndex (term_id, page_id) VALUES (?, ?)", (termID, pageID))

        # commit changes to the database
        conn.commit()

        # add to visited set
        visited.add(curUrl)

        if(debug):
            print("Remaining pages to scrape: " + (str(targetVisited - len(visited))))

        # move on to the next page to keep scraping until we reach the target number of pages or we run out of pages to scrape
        # we are doing so in a breadth-first manner
        # we will use a queue to keep track of the pages to scrape
        # we will use a set to keep track of the pages we have already visited (faster than checking the database)

        bfsQueue.extend(link for link in links if link not in visited)

        # start scraping
        while bfsQueue:
            nextLink = bfsQueue.popleft()
            if nextLink not in visited and not None:
                scrape(nextLink, targetVisited, pageID, bfsQueue)

# debugging execution
if debug:
    seedUrl = 'https://cse.hkust.edu.hk/'
    targetVisited = 30
    scrape(seedUrl, targetVisited, None, bfsQueue)
    cur.close()
    conn.close()
    outputDatabase()
else: # command line execution for spideyTest.py & TAs
    seedUrl = sys.argv[1]
    targetVisited = int(sys.argv[2])
    scrape(seedUrl, targetVisited, None, bfsQueue)
    cur.close()
    conn.close()
    outputDatabase()
driver.quit()