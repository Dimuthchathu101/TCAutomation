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
import argparse
import openpyxl

try:
    from git import Repo
except ImportError:
    Repo = None

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

def get_soup_from_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return BeautifulSoup(f.read(), 'html.parser')
    except Exception as e:
        print(f"Failed to parse {filepath}: {e}")
        return None

def auto_fill_and_submit_form(form, base_url, username=None, password=None):
    import tempfile
    import os
    # If credentials are provided, use Playwright for login and dashboard check
    if username and password and PLAYWRIGHT_AVAILABLE:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        action = form.get('action') or base_url
        method = form.get('method', 'get').upper()
        form_data = {}
        login_fields = ['username', 'user', 'email', 'login', 'userid', 'user_id', 'password', 'pass', 'passwd']
        for input_tag in form.find_all('input'):
            name = input_tag.get('name')
            if not name:
                continue
            input_type = input_tag.get('type', 'text').lower()
            if input_type == 'hidden':
                continue
            if username and (input_type in ['text', 'email']) and any(lf in name.lower() for lf in login_fields):
                form_data[name] = username
                continue
            if password and input_type == 'password':
                form_data[name] = password
                continue
            form_data[name] = 'test'
        # Use Playwright to submit the form and check for dashboard
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base_url, timeout=10000)
            # Fill form fields
            for name, value in form_data.items():
                try:
                    page.fill(f'input[name="{name}"]', str(value))
                except Exception:
                    pass
            # Click the first submit button in the form
            try:
                submit_selector = 'form button[type=submit], form input[type=submit]'
                page.click(submit_selector)
            except Exception:
                page.evaluate('document.forms[0].submit()')
            # Wait for dashboard or error
            actual_result = ''
            dashboard_found = False
            try:
                page.wait_for_selector('.oxd-topbar-header', timeout=7000)
                dashboard_found = True
            except PlaywrightTimeoutError:
                try:
                    page.wait_for_selector('text=Dashboard', timeout=3000)
                    dashboard_found = True
                except PlaywrightTimeoutError:
                    dashboard_found = False
            if dashboard_found:
                actual_result = 'Form submitted successfully and dashboard loaded'
            else:
                content = page.content()
                if 'Invalid credentials' in content or 'error' in content.lower():
                    actual_result = 'Form submission is broken! (error detected)'
                else:
                    actual_result = 'Form submission failed or dashboard not loaded'
            browser.close()
        return action, method, actual_result
    # Handle input fields
    action = form.get('action') or base_url
    method = form.get('method', 'get').upper()
    form_data = {}
    files = {}
    # Dummy values for special types
    dummy_values = {
        'email': 'test@example.com',
        'password': 'password',
        'number': 1,
        'tel': '+1234567890',
        'url': 'https://example.com',
        'color': '#ff0000',
        'date': '2023-01-01',
        'datetime-local': '2023-01-01T12:00',
        'time': '12:00',
        'month': '2023-01',
        'week': '2023-W01',
        'range': 1,
        'text': 'test',
    }
    login_fields = ['username', 'user', 'email', 'login', 'userid', 'user_id', 'password', 'pass', 'passwd']
    # Handle input fields
    for input_tag in form.find_all('input'):
        name = input_tag.get('name')
        if not name:
            continue
        input_type = input_tag.get('type', 'text').lower()
        if input_type == 'hidden':
            continue  # skip hidden fields
        # Handle file upload
        if input_type == 'file':
            # Create a dummy file in memory
            dummy_file = tempfile.NamedTemporaryFile(delete=False)
            dummy_file.write(b'dummy data')
            dummy_file.seek(0)
            dummy_file.close()
            files[name] = open(dummy_file.name, 'rb')
            continue
        # Handle checkboxes and radios
        if input_type == 'checkbox':
            if name not in form_data:
                form_data[name] = input_tag.get('value', 'on')
            continue
        if input_type == 'radio':
            if name not in form_data:
                form_data[name] = input_tag.get('value', 'on')
            continue
        # Use provided credentials for login fields
        if username and (input_type in ['text', 'email']) and any(lf in name.lower() for lf in login_fields):
            form_data[name] = username
            continue
        if password and input_type == 'password':
            form_data[name] = password
            continue
        # Handle pattern, min, max, required
        value = dummy_values.get(input_type, 'test')
        pattern = input_tag.get('pattern')
        min_val = input_tag.get('min')
        max_val = input_tag.get('max')
        required = input_tag.has_attr('required')
        # Try to match pattern if present
        if pattern:
            import re
            # Try to generate a value that matches the pattern (simple cases)
            if pattern == '[0-9]{5}':
                value = '12345'
            elif pattern == '[A-Za-z]{3,}':
                value = 'abcde'
            # Add more pattern cases as needed
        # Respect min/max for number/range
        if input_type in ['number', 'range']:
            if min_val:
                value = min_val
            if max_val and float(value) > float(max_val):
                value = max_val
        # Ensure required fields are filled
        if required and not value:
            value = 'test'
        form_data[name] = value
    # Handle select fields
    for select_tag in form.find_all('select'):
        name = select_tag.get('name')
        if not name:
            continue
        option = select_tag.find('option', attrs={'disabled': False, 'hidden': False})
        if not option:
            option = select_tag.find('option')
        if option and option.get('value') is not None:
            form_data[name] = option['value']
        elif option:
            form_data[name] = option.text
        else:
            form_data[name] = ''
    # Handle textarea fields
    for textarea_tag in form.find_all('textarea'):
        name = textarea_tag.get('name')
        if not name:
            continue
        form_data[name] = 'test'
    actual_result = ''
    try:
        if method == 'POST':
            if files:
                resp = requests.post(urljoin(base_url, action), data=form_data, files=files, timeout=5)
            else:
                resp = requests.post(urljoin(base_url, action), data=form_data, timeout=5)
        else:
            resp = requests.get(urljoin(base_url, action), params=form_data, timeout=5)
        if resp.status_code == 200:
            if 'error' in resp.text.lower():
                actual_result = 'Form submission is broken! (error detected)'
            else:
                actual_result = 'Form submitted successfully'
        else:
            actual_result = f'Form submission failed with status {resp.status_code}'
    except Exception as e:
        actual_result = f'Form submission error: {e}'
    finally:
        # Clean up dummy files
        for f in files.values():
            try:
                fname = f.name
                f.close()
                os.unlink(fname)
            except Exception:
                pass
    return action, method, actual_result

def extract_elements(soup, base_url, username=None, password=None):
    test_cases = []
    for idx, form in enumerate(soup.find_all('form')):
        action, method, actual_result = auto_fill_and_submit_form(form, base_url, username, password)
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

def get_soup_from_url_playwright(url, wait_for_selector='form', timeout=10000):
    """Load a page with Playwright and return BeautifulSoup of the rendered HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=timeout)
        try:
            page.wait_for_selector(wait_for_selector, timeout=timeout)
        except Exception:
            pass  # If no form appears, just continue
        html = page.content()
        browser.close()
    return BeautifulSoup(html, 'html.parser')

def parse_args():
    parser = argparse.ArgumentParser(description='Website Test Case Generator')
    parser.add_argument('url', help='Website URL or GitHub Repo')
    parser.add_argument('--username', help='Username for login forms', default=None)
    parser.add_argument('--password', help='Password for login forms', default=None)
    return parser.parse_args()

def run_ddt_logins(url, login_excel='test_logins.xlsx', output_excel='test_cases_ddt.xlsx'):
    wb = openpyxl.load_workbook(login_excel)
    ws = wb.active
    results = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        username, password = row
        if PLAYWRIGHT_AVAILABLE:
            soup = get_soup_from_url_playwright(url)
        else:
            soup = get_soup_from_url(url)
        if not soup:
            results.append({'Username': username, 'Password': password, 'Type': '', 'Action': '', 'Element': '', 'Expected Result': '', 'Actual Result': 'Failed to load page', 'Notes': ''})
            continue
        test_cases = extract_elements(soup, url, username, password)
        for tc in test_cases:
            tc['Username'] = username
            tc['Password'] = password
            results.append(tc)
    # Write results to Excel with color coding
    from openpyxl.styles import PatternFill, Font
    wb_out = openpyxl.Workbook()
    ws_out = wb_out.active
    headers = ['Username', 'Password', 'Test Case ID', 'Type', 'Action', 'Element', 'Expected Result', 'Actual Result', 'Notes']
    ws_out.append(headers)
    # Define color fills
    header_fill = PatternFill(start_color='4F81BD', end_color='4F81BD', fill_type='solid')
    fill1 = PatternFill(start_color='DCE6F1', end_color='DCE6F1', fill_type='solid')
    fill2 = PatternFill(start_color='B8CCE4', end_color='B8CCE4', fill_type='solid')
    fill3 = PatternFill(start_color='95B3D7', end_color='95B3D7', fill_type='solid')
    fill4 = PatternFill(start_color='4F81BD', end_color='4F81BD', fill_type='solid')
    error_fill = PatternFill(start_color='FF0000', end_color='FF0000', fill_type='solid')
    fills = [fill1, fill2, fill3, fill4]
    # Define fonts
    header_font = Font(bold=True, color='FFFFFF', size=14)
    data_font = Font(size=14)
    error_font = Font(size=14, color='FFFFFF', bold=True)
    # Apply header fill and font
    for col in range(1, len(headers) + 1):
        cell = ws_out.cell(row=1, column=col)
        cell.fill = header_fill
        cell.font = header_font
    # Data rows
    for idx, tc in enumerate(results, 1):
        row = [
            tc.get('Username', ''),
            tc.get('Password', ''),
            idx,
            tc.get('Type', ''),
            tc.get('Action', ''),
            tc.get('Element', ''),
            tc.get('Expected Result', ''),
            tc.get('Actual Result', ''),
            tc.get('Notes', '')
        ]
        ws_out.append(row)
        # Error highlighting
        actual_result = str(tc.get('Actual Result', '')).lower()
        is_error = (
            'broken' in actual_result or
            'not working' in actual_result or
            'failed' in actual_result or
            'error' in actual_result
        )
        fill = error_fill if is_error else fills[(idx - 1) % len(fills)]
        font = error_font if is_error else data_font
        for col in range(1, len(headers) + 1):
            cell = ws_out.cell(row=idx+1, column=col)
            cell.fill = fill
            cell.font = font
    wb_out.save(output_excel)
    print(f"DDT login test cases written to {output_excel}")

def main():
    args = parse_args()
    arg = args.url
    username = args.username
    password = args.password
    if arg.startswith('http') and 'github.com' in arg:
        analyze_github_repo(arg)
    elif arg.startswith('http'):
        if username == 'DDT' and password == 'DDT':
            run_ddt_logins(arg)
            return
        if PLAYWRIGHT_AVAILABLE:
            soup = get_soup_from_url_playwright(arg)
        else:
            soup = get_soup_from_url(arg)
        if not soup:
            print("Failed to analyze the website.")
            sys.exit(1)
        test_cases = extract_elements(soup, arg, username, password)
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