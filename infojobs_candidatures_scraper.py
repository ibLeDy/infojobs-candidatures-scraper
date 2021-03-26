import os
import re
import json
import time
import argparse
import webbrowser
from datetime import datetime
from collections import namedtuple

import pyautogui
import pyperclip
from bs4 import BeautifulSoup
from jinja2 import Template
from rich.table import Table
from rich.console import Console

BASE_URL = 'https://www.infojobs.net'
CANDIDATURES_URL = f'{BASE_URL}/candidate/applications/list.xhtml'
NEXT_PAGE_URL = f'{CANDIDATURES_URL}?&showDescartadas=false&pag='
NEXT_PAGE_RE = r'^.*pag='
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
DATA_DIR = f'{BASE_DIR}/data'
TEMPLATE_FILE = f'{DATA_DIR}/template.html'
RESULTS_DATA_FILE = f'{DATA_DIR}/results.json'
RESULTS_PAGE_FILE = f'{DATA_DIR}/results.html'
RESULTS_SORTED_PAGE_FILE = f'{DATA_DIR}/results_sorted.html'

__version_info__ = (0, 1, 0)
__version__ = '.'.join(map(str, __version_info__))


class Candidatures:
    def __init__(self, delay, sort):
        self.delay = delay
        self.sort = sort
        self.past_candidatures = load_past_candidatures()
        self.all_candidatures = self.parse_my_candidatures(CANDIDATURES_URL)

    def _candidature_matches_past_candidature(self, title, company):
        if self.past_candidatures is not None:
            for candidature in self.past_candidatures:
                if candidature['title'] == title and candidature['company_name'] == company:
                    return candidature

    def parse_my_candidatures(self, page, parsed_candidatures=None):
        soup = BeautifulSoup(get_page(page, self.delay), 'html.parser')
        all_candidatures = []
        if parsed_candidatures is not None:
            all_candidatures.extend(parsed_candidatures)

        error_count = 0
        try:
            for item in soup.find('ul', {'id': 'application-list'}).find_all('li'):
                if item.attrs.get('id', '').startswith('inscription'):
                    last_seen_item, last_status_item = item.div.select('ul > li')
                    last_seen = last_seen_item.span.text
                    last_status = last_status_item.span.attrs['class'][-1]

                    candidature_title = item.div.h2.a.span.text
                    company_name = item.div.h3.span.a.span.text

                    past_candidature = self._candidature_matches_past_candidature(
                        candidature_title, company_name
                    )
                    if past_candidature is not None:
                        if (past_candidature['events'][0]['icon'].startswith(last_status)
                           or last_status == 'iconfont-Check'):  # noqa
                            all_candidatures.append(past_candidature)
                            continue

                    details_url = BASE_URL + item.div.h2.a.attrs['href']
                    details = self.parse_individual_candidature_page(
                        get_page(details_url, self.delay)
                    )

                    all_candidatures.append(
                        {
                            'title': candidature_title,
                            'company_name': company_name,
                            'last_seen': last_seen,
                            'location': details['location'],
                            'registered_and_vacancies': details['registered_and_vacancies'],
                            'status': details['status'],
                            'details_url': details_url,
                            'offer_url': details['offer_url'],
                            'events': details['events'],
                        }
                    )
        except AttributeError as e:
            if error_count < 3:
                print(f'Error parsing candidature, error {error_count}:', e)
                error_count += 1
            else:
                print('Error parsing candidature:', e)

        next_button = soup.find('button', {'class': 'pagination-btn--next'})
        if next_button:
            index = re.search(NEXT_PAGE_RE, next_button.attrs['onclick']).span()[-1]
            page_number = next_button.attrs['onclick'][index]
            return self.parse_my_candidatures(
                f'{NEXT_PAGE_URL}{page_number}',
                all_candidatures
            )

        return all_candidatures

    def parse_individual_candidature_page(self, page):
        soup = BeautifulSoup(page, 'html.parser')
        details = soup.find('div', {'class': 'job-list'})
        location, registered_and_vacancies = details.div.select('ul > li')
        candidature_details = {
            'location': location.text,
            'registered_and_vacancies': registered_and_vacancies.text,
            'offer_url': details.h2.a.attrs['href'],
            'status': None,
            'events': []
        }
        for item in soup.find_all('li', {'class': 'timeline-event'}):
            candidature_details['events'].append(
                {
                    'event': item.p.text,
                    'date': item.time.text,
                    'icon': ' '.join(item.span.attrs['class'][1:]),
                }
            )

        candidature_details['status'] = self.compute_candidature_status(
            candidature_details['events']
        )
        return candidature_details

    def compute_candidature_status(self, events):
        Strength = namedtuple('Strength', ['name', 'value', 'emoji'])
        strengths = {
            'iconfont-Check focus': Strength('Applied', 0, '✅'),
            'iconfont-Viewdetails focus': Strength('CV Read', 1, '👀'),
            'iconfont-Check marked': Strength('Included', 2, '✔️'),
            'iconfont-Close alert': Strength('Rejected', 3, '❌'),
        }
        return dict(max(
            [strengths[event['icon']] for event in events], key=lambda x: x.value
        )._asdict())


def can_update_results(force):
    if force:
        return True

    can_update = False
    if os.path.isfile(RESULTS_DATA_FILE):
        message = 'Existing results will be overwritten, continue? [Y/N]: '
        if input(message).lower() != 'y':
            return False
        can_update = True

    try:
        modified = datetime.fromtimestamp(os.path.getmtime(RESULTS_DATA_FILE))
        if (datetime.now() - modified).seconds >= 3600:  # NOTE: one hour
            print('Results are older than an hour, generating new results')
            can_update = True
    except FileNotFoundError:
        print('Previous results not found, generating new ones')
        can_update = True

    return can_update


def load_past_candidatures():
    if os.path.isfile(RESULTS_DATA_FILE):
        with open(RESULTS_DATA_FILE, 'r') as fp:
            past_candidatures = json.load(fp)
        return past_candidatures


def get_page(url, delay):
    webbrowser.open_new_tab(url)
    time.sleep(delay)
    pyautogui.hotkey('ctrl', 'u')
    time.sleep(delay / 2)
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.hotkey('ctrl', 'c')
    time.sleep(delay / 2)
    pyautogui.hotkey('ctrl', 'w')
    pyautogui.hotkey('ctrl', 'w')
    return pyperclip.paste()


def sort_candidatures_by_status(all_candidatures):
    categories = {
        '✔️': [],
        '👀': [],
        '✅': [],
        '❌': [],
    }

    for candidature in all_candidatures:
        categories[candidature['status']['emoji']].append(candidature)

    return [
        *categories['✔️'],
        *categories['👀'],
        *categories['✅'],
        *categories['❌'],
    ]


def build_results_page():
    with open(TEMPLATE_FILE, 'r') as fp:
        template = Template(fp.read())

    with open(RESULTS_DATA_FILE, 'r') as fp:
        all_candidatures = json.load(fp)

    with open(RESULTS_PAGE_FILE, 'w') as fp:
        fp.write(template.render({
            'headers': ['Title', 'Company', 'Location', 'Status'],
            'items': all_candidatures,
        }))

    with open(RESULTS_SORTED_PAGE_FILE, 'w') as fp:
        fp.write(template.render({
            'headers': ['Title', 'Company', 'Location', 'Status'],
            'items': sort_candidatures_by_status(all_candidatures),
        }))


def save_results_to_disk(results):
    with open(RESULTS_DATA_FILE, 'w') as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2)


def print_table(sort):
    with open(RESULTS_DATA_FILE, 'r') as fp:
        all_candidatures = json.load(fp)

    table = Table()
    table.add_column('Title')
    table.add_column('Company')
    table.add_column('Status')

    if sort:
        all_candidatures = sort_candidatures_by_status(all_candidatures)

    for candidature in all_candidatures:
        style = ''
        if candidature['status']['emoji'] == '✔️':
            style = 'green'
        elif candidature['status']['emoji'] == '❌':
            style = 'red'

        table.add_row(
            candidature['title'],
            candidature['company_name'],
            candidature['status']['emoji'],
            style=style,
        )
    Console().print(table)


def main():
    parser = argparse.ArgumentParser(
        prog=os.path.splitext(__file__)[0].replace('_', '-'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--delay',
        type=int,
        default=2,
        help='specific delay for browser actions, default is 2',
    )
    parser.add_argument(
        '--display',
        action='store_true',
        help='open existing results in your default browser',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='ignore past results and generate new ones',
    )
    parser.add_argument(
        '--print',
        action='store_true',
        help='print existing results to stdout',
    )
    parser.add_argument(
        '--sort',
        action='store_true',
        help='display/print candidatures sorted by status',
    )
    parser.add_argument(
        '--version',
        action='version',
        version=f'%(prog)s v{__version__}',
        help='show program\'s version number and exit',
    )
    args = parser.parse_args()

    if args.display:
        if args.sort:
            webbrowser.open(RESULTS_SORTED_PAGE_FILE)
        else:
            webbrowser.open(RESULTS_PAGE_FILE)
        return
    elif args.print:
        print_table(args.sort)
        return

    if can_update_results(args.force):
        candidatures = Candidatures(args.delay, args.sort)
        save_results_to_disk(candidatures.all_candidatures)
        build_results_page()
        webbrowser.open(RESULTS_PAGE_FILE)


if __name__ == '__main__':
    main()
