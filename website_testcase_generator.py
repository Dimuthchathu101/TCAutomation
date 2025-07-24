import os
import tempfile
import glob
import shutil
import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from urllib.parse import urljoin
import sys
import re
from openpyxl.styles import PatternFill, Font

try:
    from git import Repo
except ImportError:
    Repo = None

def get_soup_from_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return BeautifulSoup(f.read(), 'html.parser')
    except Exception as e:
        print(f"Failed to parse {filepath}: {e}")
        return None

def extract_elements(soup, base_url):
    test_cases = []
    for idx, form in enumerate(soup.find_all('form')):
        action = form.get('action') or base_url
        method = form.get('method', 'get').upper()
        # Prepare form data
        form_data = {}
        for input_tag in form.find_all('input'):
            name = input_tag.get('name')
            if not name:
                continue
            input_type = input_tag.get('type', 'text')
            if input_type == 'email':
                form_data[name] = 'test@example.com'
            else:
                form_data[name] = 'test'
        # Try to submit the form
        actual_result = ''
        try:
            if method == 'POST':
                resp = requests.post(urljoin(base_url, action), data=form_data, timeout=5)
            else:
                resp = requests.get(urljoin(base_url, action), params=form_data, timeout=5)
            if resp.status_code == 200:
                # Check for error message in response
                if 'error' in resp.text.lower():
                    actual_result = 'Form submission is broken! (error detected)'
                else:
                    actual_result = 'Form submitted successfully'
            else:
                actual_result = f'Form submission failed with status {resp.status_code}'
        except Exception as e:
            actual_result = f'Form submission error: {e}'
        test_cases.append({
            'Type': 'Form',
            'Action': f"Submit {method} form",
            'Element': action,
            'Expected Result': 'Form submitted successfully',
            'Actual Result': actual_result,
            'Notes': f"Form #{idx+1} on page"
        })
    for idx, button in enumerate(soup.find_all('button')):
        btn_text = button.get_text(strip=True)
        test_cases.append({
            'Type': 'Button',
            'Action': 'Click button',
            'Element': btn_text or 'Unnamed button',
            'Expected Result': 'Button click triggers expected action',
            'Actual Result': 'Button is not working!',
            'Notes': f"Button #{idx+1} on page"
        })
    for idx, link in enumerate(soup.find_all('a', href=True)):
        href = urljoin(base_url, link['href'])
        link_text = link.get_text(strip=True)
        test_cases.append({
            'Type': 'Link',
            'Action': 'Click link',
            'Element': link_text or href,
            'Expected Result': 'Navigates to linked page',
            'Actual Result': 'Navigates to linked page',
            'Notes': f"Link #{idx+1} on page"
        })
    return test_cases

def extract_elements_from_jsx(js_content, base_url):
    test_cases = []
    # Find <form ...>
    for idx, match in enumerate(re.finditer(r'<form[^>]*>', js_content, re.IGNORECASE)):
        test_cases.append({
            'Type': 'Form',
            'Action': 'Submit form',
            'Element': 'JSX/JS Form',
            'Expected Result': 'Form submitted successfully',
            'Actual Result': 'Form submission is broken!',
            'Notes': f"Form #{idx+1} in JS/JSX file"
        })
    # Find <button ...>
    for idx, match in enumerate(re.finditer(r'<button[^>]*>(.*?)</button>', js_content, re.IGNORECASE|re.DOTALL)):
        btn_text = match.group(1).strip()
        test_cases.append({
            'Type': 'Button',
            'Action': 'Click button',
            'Element': btn_text or 'Unnamed button',
            'Expected Result': 'Button click triggers expected action',
            'Actual Result': 'Button is not working!',
            'Notes': f"Button #{idx+1} in JS/JSX file"
        })
    # Find <a ...>
    for idx, match in enumerate(re.finditer(r'<a[^>]*>(.*?)</a>', js_content, re.IGNORECASE|re.DOTALL)):
        link_text = match.group(1).strip()
        test_cases.append({
            'Type': 'Link',
            'Action': 'Click link',
            'Element': link_text or 'Unnamed link',
            'Expected Result': 'Navigates to linked page',
            'Actual Result': 'Navigates to linked page',
            'Notes': f"Link #{idx+1} in JS/JSX file"
        })
    return test_cases

def write_to_excel(test_cases, filename='test_cases.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Test Cases'
    headers = ['Test Case ID', 'Type', 'Action', 'Element', 'Expected Result', 'Actual Result', 'Notes']
    ws.append(headers)
    # Define color fills
    header_fill = PatternFill(start_color='4F81BD', end_color='4F81BD', fill_type='solid')
    fill1 = PatternFill(start_color='DCE6F1', end_color='DCE6F1', fill_type='solid')
    fill2 = PatternFill(start_color='B8CCE4', end_color='B8CCE4', fill_type='solid')
    fill3 = PatternFill(start_color='95B3D7', end_color='95B3D7', fill_type='solid')
    fill4 = PatternFill(start_color='4F81BD', end_color='4F81BD', fill_type='solid')
    fills = [fill1, fill2, fill3, fill4]
    # Define fonts
    header_font = Font(bold=True, color='FFFFFF', size=14)
    data_font = Font(size=14)
    # Apply header fill and font
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
    # Data rows
    for idx, tc in enumerate(test_cases, 1):
        row = [
            idx,
            tc['Type'],
            tc['Action'],
            tc['Element'],
            tc['Expected Result'],
            tc['Actual Result'],
            tc['Notes']
        ]
        ws.append(row)
        fill = fills[(idx - 1) % len(fills)]
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=idx+1, column=col)
            cell.fill = fill
            cell.font = data_font
    wb.save(filename)
    print(f"Test cases written to {filename}")

def clone_github_repo(repo_url, dest_dir):
    if Repo is None:
        print("gitpython is not installed. Please install it with 'pip install gitpython'.")
        sys.exit(1)
    try:
        Repo.clone_from(repo_url, dest_dir)
        print(f"Cloned {repo_url} to {dest_dir}")
    except Exception as e:
        print(f"Failed to clone repo: {e}")
        sys.exit(1)

def analyze_github_repo(repo_url):
    temp_dir = tempfile.mkdtemp()
    try:
        clone_github_repo(repo_url, temp_dir)
        html_files = [y for x in os.walk(temp_dir) for y in glob.glob(os.path.join(x[0], '*.html'))]
        js_files = [y for x in os.walk(temp_dir) for y in glob.glob(os.path.join(x[0], '*.js'))]
        jsx_files = [y for x in os.walk(temp_dir) for y in glob.glob(os.path.join(x[0], '*.jsx'))]
        # Filter out node_modules and .git
        html_files = [f for f in html_files if 'node_modules' not in f and '.git' not in f]
        js_files = [f for f in js_files if 'node_modules' not in f and '.git' not in f]
        jsx_files = [f for f in jsx_files if 'node_modules' not in f and '.git' not in f]
        print(f"Found {len(html_files)} HTML, {len(js_files)} JS, {len(jsx_files)} JSX files.")
        all_test_cases = []
        for html_file in html_files:
            print(f"Analyzing HTML: {html_file}")
            soup = get_soup_from_file(html_file)
            if soup:
                test_cases = extract_elements(soup, base_url=html_file)
                all_test_cases.extend(test_cases)
        for js_file in js_files + jsx_files:
            print(f"Analyzing JS/JSX: {js_file}")
            try:
                with open(js_file, 'r', encoding='utf-8', errors='ignore') as f:
                    js_content = f.read()
                test_cases = extract_elements_from_jsx(js_content, base_url=js_file)
                all_test_cases.extend(test_cases)
            except Exception as e:
                print(f"Failed to analyze {js_file}: {e}")
        write_to_excel(all_test_cases)
    finally:
        shutil.rmtree(temp_dir)

def main():
    if len(sys.argv) < 2:
        print("Usage: python website_testcase_generator.py <URL or GitHub Repo>")
        sys.exit(1)
    arg = sys.argv[1]
    if arg.startswith('http') and 'github.com' in arg:
        analyze_github_repo(arg)
    elif arg.startswith('http'):
        soup = get_soup_from_url(arg)
        if not soup:
            print("Failed to analyze the website.")
            sys.exit(1)
        test_cases = extract_elements(soup, arg)
        write_to_excel(test_cases)
    else:
        print("Invalid argument. Please provide a website URL or GitHub repo URL.")
        sys.exit(1)

def get_soup_from_url(url):
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None

if __name__ == "__main__":
    main() 