"""VID's public form in a fresh browser session, with immutable evidence."""
import csv
import hashlib
import io
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .normalize import number

URL = 'https://www6.vid.gov.lv/NPAR'
FIELDS = ['registration_number','effective_date','published_debt_amount',
          'publication_threshold','query_status','evidence_url']
MONTHS = ['janvāra','februāra','marta','aprīļa','maija','jūnija','jūlija',
          'augusta','septembra','oktobra','novembra','decembra']


def query_date(today=None, holiday_dates=None):
    if today is None:
        now = datetime.now(ZoneInfo('Europe/Riga'))
        today = now.date() - timedelta(days=int(now.hour < 7))
    if holiday_dates is None:
        import holidays
        holiday_dates = holidays.country_holidays('LV', years=[today.year-1,today.year])
    candidate = today
    # The last two completed working days are unavailable; use the third.
    for _ in range(3):
        candidate -= timedelta(days=1)
        while candidate.weekday() >= 5 or candidate in holiday_dates:
            candidate -= timedelta(days=1)
    return candidate


def parse_result(text, registration_number, effective_date):
    text = ' '.join(text.split())
    stamp = re.search(r'(\d{4})\.\s*gada\s+(\w+)\s+(\d{1,2})\.\s*datumā', text)
    if not stamp or stamp[2] not in MONTHS:
        raise ValueError('VID result date is missing')
    actual = date(int(stamp[1]), MONTHS.index(stamp[2])+1, int(stamp[3]))
    if actual != effective_date or not re.search(r'(?<!\d)'+re.escape(registration_number)+r'(?!\d)',text):
        raise ValueError('VID result identity/date mismatch')
    no_debt = re.search(r'nav VID administrēto nodokļu \(nodevu\) parāda, kas kopsummā pārsniedz 150(?:[.,]00)? euro',text)
    if no_debt:
        status, amount = 'NO_PUBLISHED_DEBT_ABOVE_THRESHOLD', None
    else:
        # Fail closed on unknown wording rather than reading a historical amount.
        match = re.search(r'ir VID administrēto nodokļu \(nodevu\) parāds[^\d]*([\d\s]+[.,]\d{2})\s*(?:euro|EUR)',text)
        if not match:
            raise ValueError('Unrecognized VID debt statement')
        from decimal import Decimal
        amount = number(match[1])
        if Decimal(amount) <= 150:
            raise ValueError('Unexpected published debt amount')
        status = 'PUBLISHED_DEBT'
    return dict(registration_number=registration_number,effective_date=actual.isoformat(),
                published_debt_amount=amount,publication_threshold='150',
                query_status=status,evidence_url=URL)


def save_object(data_dir, content, suffix):
    sha = hashlib.sha256(content).hexdigest()
    path = Path('raw')/'objects'/(sha+suffix)
    target = data_dir/path
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        target.write_bytes(content)
    return {'path':path.as_posix(),'sha256':sha,'size':len(content)}


def collect_browser(companies, data_dir):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from pypdf import PdfReader

    requested = os.getenv('VID_DEBT_QUERY_DATE')
    effective = date.fromisoformat(requested) if requested else query_date()
    rows, evidence = {}, []
    with tempfile.TemporaryDirectory(prefix='vid-') as folder:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--disable-dev-shm-usage')
        # Docker supplies process isolation; its default seccomp blocks Chrome namespaces.
        if Path('/.dockerenv').exists():
            options.add_argument('--no-sandbox')
        if os.getenv('CHROME_BIN'):
            options.binary_location = os.environ['CHROME_BIN']
        options.add_experimental_option('prefs', {'download.default_directory':folder,
            'download.prompt_for_download':False,'plugins.always_open_pdf_externally':True})
        service = Service(os.environ['CHROMEDRIVER_BIN']) if os.getenv('CHROMEDRIVER_BIN') else Service()
        with webdriver.Chrome(service=service,options=options) as driver:
            driver.set_page_load_timeout(60)
            wait = WebDriverWait(driver,45)
            for company in companies:
                reg = company['registration_number']
                item = {'registration_number':reg,'effective_date':effective.isoformat(),
                        'retrieved_at':datetime.now(ZoneInfo('UTC')).isoformat()}
                try:
                    if not company.get('name'):
                        raise ValueError('Company name required by VID')
                    driver.get(URL)
                    driver.find_element(By.ID,'IsLegalPerson').click()
                    for key,value in [('Name',company['name']),('Code',reg),('QueryDate',effective.strftime('%d.%m.%Y'))]:
                        field = driver.find_element(By.ID,key)
                        field.clear()
                        field.send_keys(value)
                    if any(e.is_displayed() for e in driver.find_elements(By.ID,'recaptcha_response_field')):
                        raise ValueError('HUMAN_VERIFICATION_REQUIRED')
                    driver.find_element(By.ID,'btnSearch').click()
                    wait.until(lambda d: d.find_element(By.ID,'data').text.strip())
                    content = driver.find_element(By.ID,'data')
                    item['html'] = save_object(data_dir,content.get_attribute('outerHTML').encode('utf-8'),'.html')
                    # Only parse the current statement, excluding history and notices.
                    headings = content.find_elements(By.TAG_NAME,'h2')
                    statements = [h.text for h in headings if 'datumā' in h.text]
                    if len(statements)!=1:
                        raise ValueError('VID current statement unavailable')
                    row = parse_result(statements[0],reg,effective)
                    before = set(Path(folder).glob('*.pdf'))
                    driver.find_element(By.CSS_SELECTOR,'#frmNPARResult input[type="submit"], #frmNPARResult button').click()
                    deadline = time.monotonic()+60
                    pdf = None
                    while time.monotonic()<deadline:
                        files = set(Path(folder).glob('*.pdf'))-before
                        if files and not list(Path(folder).glob('*.crdownload')):
                            pdf = next(iter(files)).read_bytes()
                            break
                        time.sleep(.25)
                    if not pdf or not pdf.startswith(b'%PDF-'):
                        raise ValueError('VID PDF download unavailable')
                    pdf_text = '\n'.join(p.extract_text() or '' for p in PdfReader(io.BytesIO(pdf)).pages)
                    pdf_row = parse_result(pdf_text,reg,effective)
                    if pdf_row != row:
                        raise ValueError('VID HTML/PDF mismatch')
                    item['pdf'] = save_object(data_dir,pdf,'.pdf')
                    rows[reg] = row
                    item['status'] = row['query_status']
                except Exception as exc:
                    item['status'] = 'NOT_CHECKED'
                    item['reason'] = str(exc) if isinstance(exc,ValueError) else type(exc).__name__
                evidence.append(item)
                print(f"vid_debt: {reg} {item['status']} {item.get('reason','')}",flush=True)
                # Keep requests sequential and avoid an unbounded series of failures.
                if item.get('reason') == 'HUMAN_VERIFICATION_REQUIRED':
                    break
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output,fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows.values())
    csv_meta = save_object(data_dir,output.getvalue().encode('utf-8'),'.csv')
    return data_dir/csv_meta['path'], evidence
