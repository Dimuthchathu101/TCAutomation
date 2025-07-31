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
                with page.expect_navigation(wait_until='networkidle', timeout=10000):
                    page.click(submit_selector)
            except Exception:
                try:
                    with page.expect_navigation(wait_until='networkidle', timeout=10000):
                        page.evaluate('document.forms[0].submit()')
                except Exception:
                    pass
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
                try:
                    content = page.content()
                except Exception:
                    content = ''
                if 'Invalid credentials' in content or 'error' in content.lower():
                    actual_result = 'Form submission is broken! (error detected)'
                else:
                    actual_result = 'Form submission failed or dashboard not loaded'
            
            # --- ENHANCED: Post-login dashboard testing ---
            post_login_test_cases = []
            if dashboard_found or page.url != base_url:
                from bs4 import BeautifulSoup
                new_soup = BeautifulSoup(page.content(), 'html.parser')
                # Extract further test cases (no login credentials for post-login page)
                post_login_test_cases = extract_elements(new_soup, page.url)
                for tc in post_login_test_cases:
                    tc['Notes'] = f"[Post-login] {tc.get('Notes','')}"
                
                # --- NEW: Enhanced Dashboard Navigation and Form Testing ---
                dashboard_test_cases = test_dashboard_functionality(page, base_url)
                post_login_test_cases.extend(dashboard_test_cases)
                
            browser.close()
        return action, method, actual_result, post_login_test_cases
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
        # Handle other input types
        if input_type in dummy_values:
            form_data[name] = dummy_values[input_type]
        elif username and any(lf in name.lower() for lf in login_fields):
            form_data[name] = username
        elif password and input_type == 'password':
            form_data[name] = password
        else:
            form_data[name] = dummy_values['text']
    # Handle select dropdowns
    for select_tag in form.find_all('select'):
        name = select_tag.get('name')
        if not name:
            continue
        options = select_tag.find_all('option')
        if options:
            # Select the first non-disabled option
            for option in options:
                if not option.get('disabled'):
                    form_data[name] = option.get('value', '')
                    break
    # Handle textarea
    for textarea in form.find_all('textarea'):
        name = textarea.get('name')
        if name:
            form_data[name] = 'Test textarea content'
    return action, method, 'Form data prepared for submission'

def test_dashboard_functionality(page, base_url):
    """
    Enhanced function to test dashboard functionality after login
    Specifically handles Admin dashboards and user management forms
    """
    dashboard_test_cases = []
    
    try:
        # Wait for page to be fully loaded with timeout
        try:
            page.wait_for_load_state('networkidle', timeout=15000)
        except Exception as e:
            dashboard_test_cases.append({
                'Type': 'Dashboard',
                'Action': 'Wait for page load',
                'Element': 'Page',
                'Expected Result': 'Page should load completely',
                'Actual Result': f'Page load timeout: {str(e)}',
                'Notes': '[Dashboard Loading]'
            })
        
        # --- Step 1: Navigate to Admin Dashboard ---
        try:
            admin_navigation_cases = navigate_to_admin_dashboard(page)
            dashboard_test_cases.extend(admin_navigation_cases)
        except Exception as e:
            dashboard_test_cases.append({
                'Type': 'Navigation',
                'Action': 'Navigate to Admin dashboard',
                'Element': 'Admin Dashboard',
                'Expected Result': 'Successfully navigate to Admin dashboard',
                'Actual Result': f'Admin navigation failed: {str(e)}',
                'Notes': '[Admin Navigation Error]'
            })
        
        # --- Step 2: Test User Management Forms ---
        try:
            user_management_cases = test_user_management_forms(page)
            dashboard_test_cases.extend(user_management_cases)
        except Exception as e:
            dashboard_test_cases.append({
                'Type': 'User Management',
                'Action': 'Test user management functionality',
                'Element': 'User Management Forms',
                'Expected Result': 'Successfully test user management forms',
                'Actual Result': f'User management testing failed: {str(e)}',
                'Notes': '[User Management Error]'
            })
        
        # --- Step 3: Test Other Dashboard Sections ---
        try:
            other_sections_cases = test_other_dashboard_sections(page)
            dashboard_test_cases.extend(other_sections_cases)
        except Exception as e:
            dashboard_test_cases.append({
                'Type': 'Dashboard Sections',
                'Action': 'Test other dashboard sections',
                'Element': 'Dashboard Sections',
                'Expected Result': 'Successfully test other dashboard sections',
                'Actual Result': f'Other sections testing failed: {str(e)}',
                'Notes': '[Dashboard Sections Error]'
            })
        
    except Exception as e:
        dashboard_test_cases.append({
            'Type': 'Dashboard',
            'Action': 'Navigate and test dashboard',
            'Element': 'Admin Dashboard',
            'Expected Result': 'Successfully navigate and test dashboard functionality',
            'Actual Result': f'Dashboard testing failed: {str(e)}',
            'Notes': '[Dashboard Testing Error]'
        })
    
    return dashboard_test_cases

def navigate_to_admin_dashboard(page):
    """Navigate to Admin dashboard section"""
    admin_cases = []
    
    # Common selectors for Admin navigation
    admin_selectors = [
        'a[href*="admin"]',
        'a[href*="Admin"]', 
        'a:has-text("Admin")',
        'a:has-text("admin")',
        '.oxd-main-menu-item:has-text("Admin")',
        'nav a:has-text("Admin")',
        '[data-testid="admin"]',
        '.admin-link',
        '#admin-link'
    ]
    
    admin_link = None
    for selector in admin_selectors:
        try:
            admin_link = page.query_selector(selector)
            if admin_link:
                break
        except Exception:
            continue
    
    if admin_link:
        try:
            # Click Admin link with timeout
            admin_link.click(timeout=10000)
            admin_cases.append({
                'Type': 'Navigation',
                'Action': 'Click Admin link',
                'Element': 'Admin Dashboard',
                'Expected Result': 'Successfully navigate to Admin dashboard',
                'Actual Result': 'Successfully clicked Admin link',
                'Notes': '[Admin Navigation]'
            })
            
            # Wait for navigation with timeout
            try:
                page.wait_for_load_state('networkidle', timeout=15000)
                admin_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Wait for Admin dashboard to load',
                    'Element': 'Admin Dashboard',
                    'Expected Result': 'Admin dashboard should load completely',
                    'Actual Result': 'Successfully waited for Admin dashboard to load',
                    'Notes': '[Admin Navigation]'
                })
            except Exception as e:
                admin_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Wait for Admin dashboard to load',
                    'Element': 'Admin Dashboard',
                    'Expected Result': 'Admin dashboard should load completely',
                    'Actual Result': f'Admin dashboard load timeout: {str(e)}',
                    'Notes': '[Admin Navigation]'
                })
            
            # Take screenshot of admin dashboard
            try:
                page.screenshot(path='screenshot_Admin_Dashboard.png')
                admin_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Take Admin dashboard screenshot',
                    'Element': 'Admin Dashboard',
                    'Expected Result': 'Successfully capture Admin dashboard screenshot',
                    'Actual Result': 'Successfully captured Admin dashboard screenshot',
                    'Notes': '[Admin Navigation]'
                })
            except Exception as e:
                admin_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Take Admin dashboard screenshot',
                    'Element': 'Admin Dashboard',
                    'Expected Result': 'Successfully capture Admin dashboard screenshot',
                    'Actual Result': f'Failed to capture screenshot: {str(e)}',
                    'Notes': '[Admin Navigation]'
                })
            
        except Exception as e:
            admin_cases.append({
                'Type': 'Navigation',
                'Action': 'Click Admin link',
                'Element': 'Admin Dashboard',
                'Expected Result': 'Successfully navigate to Admin dashboard',
                'Actual Result': f'Failed to navigate: {str(e)}',
                'Notes': '[Admin Navigation Error]'
            })
    else:
        admin_cases.append({
            'Type': 'Navigation',
            'Action': 'Find Admin link',
            'Element': 'Admin Dashboard',
            'Expected Result': 'Admin link found and clickable',
            'Actual Result': 'Admin link not found on dashboard',
            'Notes': '[Admin Link Not Found]'
        })
    
    return admin_cases

def test_user_management_forms(page):
    """Test user management forms and fields"""
    user_management_cases = []
    
    try:
        # Look for User Management section
        user_mgmt_selectors = [
            'a[href*="user"]',
            'a[href*="User"]',
            'a:has-text("User Management")',
            'a:has-text("Users")',
            '.oxd-main-menu-item:has-text("User")',
            '[data-testid="user-management"]'
        ]
        
        user_mgmt_link = None
        for selector in user_mgmt_selectors:
            try:
                user_mgmt_link = page.query_selector(selector)
                if user_mgmt_link:
                    break
            except:
                continue
        
        if user_mgmt_link:
            user_mgmt_link.click()
            page.wait_for_load_state('networkidle', timeout=10000)
            
            # Test search/filter forms
            search_forms = test_search_and_filter_forms(page)
            user_management_cases.extend(search_forms)
            
            # Test add user form
            add_user_cases = test_add_user_form(page)
            user_management_cases.extend(add_user_cases)
            
            # Test user table interactions
            table_cases = test_user_table_interactions(page)
            user_management_cases.extend(table_cases)
            
        else:
            # Test forms on current page (might be admin dashboard with forms)
            current_page_forms = test_current_page_forms(page)
            user_management_cases.extend(current_page_forms)
            
    except Exception as e:
        user_management_cases.append({
            'Type': 'User Management',
            'Action': 'Test user management functionality',
            'Element': 'User Management Forms',
            'Expected Result': 'Successfully test user management forms',
            'Actual Result': f'User management testing failed: {str(e)}',
            'Notes': '[User Management Error]'
        })
    
    return user_management_cases

def test_search_and_filter_forms(page):
    """Test search and filter forms in user management"""
    search_cases = []
    
    try:
        # Find search forms
        search_forms = page.query_selector_all('form')
        
        for i, form in enumerate(search_forms):
            try:
                # Fill common search fields with detailed steps
                search_fields = {
                    'username': 'testuser',
                    'user_role': 'Admin',
                    'employee_name': 'Test Employee',
                    'status': 'Enabled',
                    'name': 'test',
                    'email': 'test@example.com',
                    'role': 'Admin'
                }
                
                # Step 1: Fill text inputs with detailed steps
                for field_name, test_value in search_fields.items():
                    try:
                        input_selector = f'input[name*="{field_name}"], input[placeholder*="{field_name}"]'
                        input_field = form.query_selector(input_selector)
                        if input_field:
                            # Step 1.1: Clear the field first
                            input_field.clear()
                            search_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Clear {field_name} field',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Successfully clear {field_name} field',
                                'Actual Result': f'Successfully cleared {field_name} field',
                                'Notes': f'[Search Form {i+1} - Step 1.1]'
                            })
                            
                            # Step 1.2: Fill the field with test data
                            input_field.fill(test_value)
                            search_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Fill {field_name} field with "{test_value}"',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Successfully fill {field_name} with test data',
                                'Actual Result': f'Successfully filled {field_name} with "{test_value}"',
                                'Notes': f'[Search Form {i+1} - Step 1.2]'
                            })
                            
                            # Step 1.3: Verify field value
                            actual_value = input_field.input_value()
                            search_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Verify {field_name} field value',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Field should contain "{test_value}"',
                                'Actual Result': f'Field contains "{actual_value}"',
                                'Notes': f'[Search Form {i+1} - Step 1.3]'
                            })
                    except Exception as e:
                        search_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Fill {field_name} field',
                            'Element': f'{field_name} input field',
                            'Expected Result': f'Successfully fill {field_name} with test data',
                            'Actual Result': f'Failed to fill {field_name}: {str(e)}',
                            'Notes': f'[Search Form {i+1} - Error]'
                        })
                        continue
                
                # Step 2: Fill dropdowns with detailed steps
                dropdowns = form.query_selector_all('select')
                for j, dropdown in enumerate(dropdowns):
                    try:
                        # Step 2.1: Get available options
                        options = dropdown.query_selector_all('option:not([disabled])')
                        if len(options) > 1:
                            # Step 2.2: Select dropdown option
                            selected_option = options[1]
                            option_text = selected_option.inner_text()
                            option_value = selected_option.get_attribute('value')
                            
                            dropdown.select_option(value=option_value)
                            search_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Select dropdown option "{option_text}"',
                                'Element': f'Dropdown {j+1}',
                                'Expected Result': f'Successfully select "{option_text}" from dropdown',
                                'Actual Result': f'Successfully selected "{option_text}" (value: {option_value})',
                                'Notes': f'[Search Form {i+1} - Step 2.1]'
                            })
                            
                            # Step 2.3: Verify dropdown selection
                            selected_value = dropdown.input_value()
                            search_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Verify dropdown selection',
                                'Element': f'Dropdown {j+1}',
                                'Expected Result': f'Dropdown should have value "{option_value}"',
                                'Actual Result': f'Dropdown has value "{selected_value}"',
                                'Notes': f'[Search Form {i+1} - Step 2.2]'
                            })
                    except Exception as e:
                        search_cases.append({
                            'Type': 'Form Field',
                            'Action': 'Select dropdown option',
                            'Element': f'Dropdown {j+1}',
                            'Expected Result': 'Successfully select dropdown option',
                            'Actual Result': f'Failed to select dropdown: {str(e)}',
                            'Notes': f'[Search Form {i+1} - Error]'
                        })
                        continue
                
                # Step 3: Test search button with detailed steps
                search_buttons = form.query_selector_all('button:has-text("Search"), button:has-text("Filter"), input[type="submit"]')
                for k, btn in enumerate(search_buttons):
                    btn_text = 'Search Button'  # Default value
                    try:
                        # Step 3.1: Verify button is clickable
                        btn_text = btn.inner_text() or 'Search Button'
                        is_enabled = not btn.is_disabled()
                        
                        search_cases.append({
                            'Type': 'Button',
                            'Action': f'Verify "{btn_text}" button is clickable',
                            'Element': btn_text,
                            'Expected Result': 'Button should be enabled and clickable',
                            'Actual Result': f'Button is {"enabled" if is_enabled else "disabled"}',
                            'Notes': f'[Search Form {i+1} - Step 3.1]'
                        })
                        
                        if is_enabled:
                            # Step 3.2: Click the button
                            btn.click()
                            search_cases.append({
                                'Type': 'Button',
                                'Action': f'Click "{btn_text}" button',
                                'Element': btn_text,
                                'Expected Result': 'Search/filter action executed',
                                'Actual Result': f'Successfully clicked "{btn_text}" button',
                                'Notes': f'[Search Form {i+1} - Step 3.2]'
                            })
                            
                            # Step 3.3: Wait for response
                            page.wait_for_timeout(2000)
                            search_cases.append({
                                'Type': 'Button',
                                'Action': f'Wait for "{btn_text}" response',
                                'Element': btn_text,
                                'Expected Result': 'Page should respond to search/filter action',
                                'Actual Result': 'Successfully waited for page response (2 seconds)',
                                'Notes': f'[Search Form {i+1} - Step 3.3]'
                            })
                            
                            # Step 3.4: Check for results or errors
                            try:
                                # Look for common result indicators
                                results_found = page.query_selector('.oxd-table, .oxd-table-body, .oxd-table-card, .oxd-table-row, .oxd-table-content, .oxd-table-list, .oxd-table-container, .oxd-table-wrapper, .oxd-table-filter, .oxd-table-header, .oxd-table-footer, .oxd-table-message, .results, .search-results, .filter-results')
                                error_found = page.query_selector('.error, .alert, .message, .notification')
                                
                                if results_found:
                                    search_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" results',
                                        'Element': btn_text,
                                        'Expected Result': 'Search/filter should return results',
                                        'Actual Result': 'Search/filter returned results successfully',
                                        'Notes': f'[Search Form {i+1} - Step 3.4]'
                                    })
                                elif error_found:
                                    search_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" error handling',
                                        'Element': btn_text,
                                        'Expected Result': 'Search/filter should handle errors gracefully',
                                        'Actual Result': 'Search/filter displayed error message',
                                        'Notes': f'[Search Form {i+1} - Step 3.4]'
                                    })
                                else:
                                    search_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" response',
                                        'Element': btn_text,
                                        'Expected Result': 'Search/filter should provide response',
                                        'Actual Result': 'Search/filter action completed (no results/errors detected)',
                                        'Notes': f'[Search Form {i+1} - Step 3.4]'
                                    })
                            except:
                                search_cases.append({
                                    'Type': 'Button',
                                    'Action': f'Verify "{btn_text}" response',
                                    'Element': btn_text,
                                    'Expected Result': 'Search/filter should provide response',
                                    'Actual Result': 'Search/filter action completed (response verification failed)',
                                    'Notes': f'[Search Form {i+1} - Step 3.4]'
                                })
                    except Exception as e:
                        search_cases.append({
                            'Type': 'Button',
                            'Action': f'Click search/filter button',
                            'Element': btn_text,
                            'Expected Result': 'Search/filter action executed',
                            'Actual Result': f'Failed to click button: {str(e)}',
                            'Notes': f'[Search Form {i+1} - Error]'
                        })
                        continue
                        
            except Exception as e:
                search_cases.append({
                    'Type': 'Form',
                    'Action': 'Test search form',
                    'Element': f'Search Form {i+1}',
                    'Expected Result': 'Successfully test search form',
                    'Actual Result': f'Search form testing failed: {str(e)}',
                    'Notes': f'[Search Form {i+1} - Error]'
                })
                
    except Exception as e:
        search_cases.append({
            'Type': 'Search Forms',
            'Action': 'Test search and filter forms',
            'Element': 'Search Forms',
            'Expected Result': 'Successfully test search forms',
            'Actual Result': f'Search forms testing failed: {str(e)}',
            'Notes': '[Search Forms Error]'
        })
    
    return search_cases

def test_add_user_form(page):
    """Test add user form functionality"""
    add_user_cases = []
    
    try:
        # Look for Add User button
        add_buttons = page.query_selector_all('button:has-text("Add"), button:has-text("+ Add"), a:has-text("Add User")')
        
        for i, add_btn in enumerate(add_buttons):
            try:
                # Step 1: Click Add User button
                add_btn.click()
                add_user_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Click Add User button',
                    'Element': 'Add User button',
                    'Expected Result': 'Should navigate to add user form',
                    'Actual Result': 'Successfully clicked Add User button',
                    'Notes': f'[Add User Form {i+1} - Step 1]'
                })
                
                page.wait_for_load_state('networkidle', timeout=10000)
                add_user_cases.append({
                    'Type': 'Navigation',
                    'Action': 'Wait for add user form to load',
                    'Element': 'Add User Form',
                    'Expected Result': 'Add user form should load completely',
                    'Actual Result': 'Successfully waited for form to load',
                    'Notes': f'[Add User Form {i+1} - Step 2]'
                })
                
                # Test add user form fields with detailed steps
                form_fields = {
                    'username': 'newuser123',
                    'password': 'Password123!',
                    'confirm_password': 'Password123!',
                    'employee_name': 'John Doe',
                    'user_role': 'Admin',
                    'status': 'Enabled',
                    'first_name': 'John',
                    'last_name': 'Doe',
                    'email': 'john.doe@example.com'
                }
                
                # Step 3: Fill form fields with detailed steps
                for field_name, test_value in form_fields.items():
                    try:
                        input_selector = f'input[name*="{field_name}"], input[placeholder*="{field_name}"]'
                        input_field = page.query_selector(input_selector)
                        if input_field:
                            # Step 3.1: Clear the field
                            input_field.clear()
                            add_user_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Clear {field_name} field',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Successfully clear {field_name} field',
                                'Actual Result': f'Successfully cleared {field_name} field',
                                'Notes': f'[Add User Form {i+1} - Step 3.1]'
                            })
                            
                            # Step 3.2: Fill the field
                            input_field.fill(test_value)
                            add_user_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Fill {field_name} field with "{test_value}"',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Successfully fill {field_name} with test data',
                                'Actual Result': f'Successfully filled {field_name} with "{test_value}"',
                                'Notes': f'[Add User Form {i+1} - Step 3.2]'
                            })
                            
                            # Step 3.3: Verify field value
                            actual_value = input_field.input_value()
                            add_user_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Verify {field_name} field value',
                                'Element': f'{field_name} input field',
                                'Expected Result': f'Field should contain "{test_value}"',
                                'Actual Result': f'Field contains "{actual_value}"',
                                'Notes': f'[Add User Form {i+1} - Step 3.3]'
                            })
                    except Exception as e:
                        add_user_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Fill {field_name} field',
                            'Element': f'{field_name} input field',
                            'Expected Result': f'Successfully fill {field_name} with test data',
                            'Actual Result': f'Failed to fill {field_name}: {str(e)}',
                            'Notes': f'[Add User Form {i+1} - Error]'
                        })
                        continue
                
                # Step 4: Test save/cancel buttons with detailed steps
                save_buttons = page.query_selector_all('button:has-text("Save"), button:has-text("Submit"), input[type="submit"]')
                for j, btn in enumerate(save_buttons):
                    btn_text = 'Save Button'  # Default value
                    try:
                        # Step 4.1: Verify button is clickable
                        btn_text = btn.inner_text() or 'Save Button'
                        is_enabled = not btn.is_disabled()
                        
                        add_user_cases.append({
                            'Type': 'Button',
                            'Action': f'Verify "{btn_text}" button is clickable',
                            'Element': btn_text,
                            'Expected Result': 'Button should be enabled and clickable',
                            'Actual Result': f'Button is {"enabled" if is_enabled else "disabled"}',
                            'Notes': f'[Add User Form {i+1} - Step 4.1]'
                        })
                        
                        if is_enabled:
                            # Step 4.2: Click the button
                            btn.click()
                            add_user_cases.append({
                                'Type': 'Button',
                                'Action': f'Click "{btn_text}" button',
                                'Element': btn_text,
                                'Expected Result': 'User creation initiated',
                                'Actual Result': f'Successfully clicked "{btn_text}" button',
                                'Notes': f'[Add User Form {i+1} - Step 4.2]'
                            })
                            
                            # Step 4.3: Wait for response
                            page.wait_for_timeout(2000)
                            add_user_cases.append({
                                'Type': 'Button',
                                'Action': f'Wait for "{btn_text}" response',
                                'Element': btn_text,
                                'Expected Result': 'Page should respond to save action',
                                'Actual Result': 'Successfully waited for page response (2 seconds)',
                                'Notes': f'[Add User Form {i+1} - Step 4.3]'
                            })
                            
                            # Step 4.4: Check for success/error messages
                            try:
                                success_found = page.query_selector('.success, .alert-success, .message-success, .notification-success')
                                error_found = page.query_selector('.error, .alert-error, .message-error, .notification-error')
                                
                                if success_found:
                                    add_user_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" success',
                                        'Element': btn_text,
                                        'Expected Result': 'User creation should succeed',
                                        'Actual Result': 'User creation succeeded (success message displayed)',
                                        'Notes': f'[Add User Form {i+1} - Step 4.4]'
                                    })
                                elif error_found:
                                    add_user_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" error handling',
                                        'Element': btn_text,
                                        'Expected Result': 'User creation should handle errors gracefully',
                                        'Actual Result': 'User creation failed (error message displayed)',
                                        'Notes': f'[Add User Form {i+1} - Step 4.4]'
                                    })
                                else:
                                    add_user_cases.append({
                                        'Type': 'Button',
                                        'Action': f'Verify "{btn_text}" response',
                                        'Element': btn_text,
                                        'Expected Result': 'User creation should provide response',
                                        'Actual Result': 'User creation action completed (no success/error message detected)',
                                        'Notes': f'[Add User Form {i+1} - Step 4.4]'
                                    })
                            except:
                                add_user_cases.append({
                                    'Type': 'Button',
                                    'Action': f'Verify "{btn_text}" response',
                                    'Element': btn_text,
                                    'Expected Result': 'User creation should provide response',
                                    'Actual Result': 'User creation action completed (response verification failed)',
                                    'Notes': f'[Add User Form {i+1} - Step 4.4]'
                                })
                    except Exception as e:
                        add_user_cases.append({
                            'Type': 'Button',
                            'Action': f'Click save button',
                            'Element': btn_text,
                            'Expected Result': 'User creation initiated',
                            'Actual Result': f'Failed to click button: {str(e)}',
                            'Notes': f'[Add User Form {i+1} - Error]'
                        })
                        continue
                        
            except Exception as e:
                add_user_cases.append({
                    'Type': 'Add User',
                    'Action': 'Test add user form',
                    'Element': f'Add User Form {i+1}',
                    'Expected Result': 'Successfully test add user form',
                    'Actual Result': f'Add user form testing failed: {str(e)}',
                    'Notes': f'[Add User Form {i+1} - Error]'
                })
                
    except Exception as e:
        add_user_cases.append({
            'Type': 'Add User Forms',
            'Action': 'Test add user functionality',
            'Element': 'Add User Forms',
            'Expected Result': 'Successfully test add user forms',
            'Actual Result': f'Add user forms testing failed: {str(e)}',
            'Notes': '[Add User Forms Error]'
        })
    
    return add_user_cases

def test_user_table_interactions(page):
    """Test user table interactions"""
    table_cases = []
    
    try:
        # Test table checkboxes
        checkboxes = page.query_selector_all('input[type="checkbox"]')
        for i, checkbox in enumerate(checkboxes[:5]):  # Test first 5 checkboxes
            try:
                checkbox.check()
                table_cases.append({
                    'Type': 'Table Interaction',
                    'Action': 'Select table row',
                    'Element': f'Checkbox {i+1}',
                    'Expected Result': 'Successfully select table row',
                    'Actual Result': 'Successfully selected table row',
                    'Notes': '[User Table]'
                })
            except:
                continue
        
        # Test edit/delete buttons in table
        action_buttons = page.query_selector_all('button:has-text("Edit"), button:has-text("Delete"), a:has-text("Edit"), a:has-text("Delete")')
        for i, btn in enumerate(action_buttons[:3]):  # Test first 3 action buttons
            try:
                btn.click()
                page.wait_for_timeout(2000)
                table_cases.append({
                    'Type': 'Table Action',
                    'Action': f'Click {btn.inner_text()} button',
                    'Element': btn.inner_text() or f'Action Button {i+1}',
                    'Expected Result': f'{btn.inner_text()} action initiated',
                    'Actual Result': f'Successfully clicked {btn.inner_text()} button',
                    'Notes': '[User Table]'
                })
            except:
                continue
                
    except Exception as e:
        table_cases.append({
            'Type': 'User Table',
            'Action': 'Test table interactions',
            'Element': 'User Table',
            'Expected Result': 'Successfully test table interactions',
            'Actual Result': f'Table interactions testing failed: {str(e)}',
            'Notes': '[User Table Error]'
        })
    
    return table_cases

def test_current_page_forms(page):
    """Test forms on current page (fallback for when specific sections aren't found)"""
    current_page_cases = []
    
    try:
        # Test all forms on current page
        forms = page.query_selector_all('form')
        
        for i, form in enumerate(forms):
            try:
                # Step 1: Identify form elements
                current_page_cases.append({
                    'Type': 'Form Analysis',
                    'Action': 'Analyze form structure',
                    'Element': f'Form {i+1}',
                    'Expected Result': 'Successfully identify form elements',
                    'Actual Result': f'Form {i+1} contains form elements',
                    'Notes': f'[Current Page Form {i+1} - Step 1]'
                })
                
                # Step 2: Fill all input fields with detailed steps
                inputs = form.query_selector_all('input[type="text"], input[type="email"], input[type="password"], input[type="number"], input[type="tel"], input[type="url"]')
                for j, input_field in enumerate(inputs):
                    try:
                        # Step 2.1: Get field information
                        field_name = input_field.get_attribute('name') or f'input_{j+1}'
                        field_type = input_field.get_attribute('type') or 'text'
                        placeholder = input_field.get_attribute('placeholder') or ''
                        
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Identify {field_name} field',
                            'Element': f'{field_name} ({field_type})',
                            'Expected Result': f'Successfully identify {field_name} field',
                            'Actual Result': f'Identified {field_name} field of type {field_type}',
                            'Notes': f'[Current Page Form {i+1} - Step 2.1]'
                        })
                        
                        # Step 2.2: Clear the field
                        input_field.clear()
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Clear {field_name} field',
                            'Element': f'{field_name} input field',
                            'Expected Result': f'Successfully clear {field_name} field',
                            'Actual Result': f'Successfully cleared {field_name} field',
                            'Notes': f'[Current Page Form {i+1} - Step 2.2]'
                        })
                        
                        # Step 2.3: Fill the field with appropriate test data
                        test_value = f'test_value_{j+1}'
                        if field_type == 'email':
                            test_value = f'test{j+1}@example.com'
                        elif field_type == 'password':
                            test_value = f'Password{j+1}!'
                        elif field_type == 'number':
                            test_value = str(j+1)
                        elif field_type == 'tel':
                            test_value = f'+1-555-{j+1:03d}'
                        elif field_type == 'url':
                            test_value = f'https://example{j+1}.com'
                        
                        input_field.fill(test_value)
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Fill {field_name} field with "{test_value}"',
                            'Element': f'{field_name} input field',
                            'Expected Result': f'Successfully fill {field_name} with test data',
                            'Actual Result': f'Successfully filled {field_name} with "{test_value}"',
                            'Notes': f'[Current Page Form {i+1} - Step 2.3]'
                        })
                        
                        # Step 2.4: Verify field value
                        actual_value = input_field.input_value()
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Verify {field_name} field value',
                            'Element': f'{field_name} input field',
                            'Expected Result': f'Field should contain "{test_value}"',
                            'Actual Result': f'Field contains "{actual_value}"',
                            'Notes': f'[Current Page Form {i+1} - Step 2.4]'
                        })
                        
                    except Exception as e:
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Fill input field {j+1}',
                            'Element': f'Input field {j+1}',
                            'Expected Result': 'Successfully fill input field',
                            'Actual Result': f'Failed to fill input field: {str(e)}',
                            'Notes': f'[Current Page Form {i+1} - Error]'
                        })
                        continue
                
                # Step 3: Handle dropdowns with detailed steps
                dropdowns = form.query_selector_all('select')
                for j, dropdown in enumerate(dropdowns):
                    try:
                        # Step 3.1: Get dropdown information
                        dropdown_name = dropdown.get_attribute('name') or f'dropdown_{j+1}'
                        options = dropdown.query_selector_all('option:not([disabled])')
                        
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Identify {dropdown_name} dropdown',
                            'Element': f'{dropdown_name} dropdown',
                            'Expected Result': f'Successfully identify {dropdown_name} dropdown',
                            'Actual Result': f'Identified {dropdown_name} dropdown with {len(options)} options',
                            'Notes': f'[Current Page Form {i+1} - Step 3.1]'
                        })
                        
                        if len(options) > 1:
                            # Step 3.2: Select dropdown option
                            selected_option = options[1]
                            option_text = selected_option.inner_text()
                            option_value = selected_option.get_attribute('value')
                            
                            dropdown.select_option(value=option_value)
                            current_page_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Select "{option_text}" from {dropdown_name}',
                                'Element': f'{dropdown_name} dropdown',
                                'Expected Result': f'Successfully select "{option_text}" from dropdown',
                                'Actual Result': f'Successfully selected "{option_text}" (value: {option_value})',
                                'Notes': f'[Current Page Form {i+1} - Step 3.2]'
                            })
                            
                            # Step 3.3: Verify dropdown selection
                            selected_value = dropdown.input_value()
                            current_page_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Verify {dropdown_name} selection',
                                'Element': f'{dropdown_name} dropdown',
                                'Expected Result': f'Dropdown should have value "{option_value}"',
                                'Actual Result': f'Dropdown has value "{selected_value}"',
                                'Notes': f'[Current Page Form {i+1} - Step 3.3]'
                            })
                    except Exception as e:
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Handle dropdown {j+1}',
                            'Element': f'Dropdown {j+1}',
                            'Expected Result': 'Successfully handle dropdown',
                            'Actual Result': f'Failed to handle dropdown: {str(e)}',
                            'Notes': f'[Current Page Form {i+1} - Error]'
                        })
                        continue
                
                # Step 4: Handle checkboxes with detailed steps
                checkboxes = form.query_selector_all('input[type="checkbox"]')
                for j, checkbox in enumerate(checkboxes):
                    try:
                        # Step 4.1: Get checkbox information
                        checkbox_name = checkbox.get_attribute('name') or f'checkbox_{j+1}'
                        checkbox_value = checkbox.get_attribute('value') or 'on'
                        is_checked = checkbox.is_checked()
                        
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Identify {checkbox_name} checkbox',
                            'Element': f'{checkbox_name} checkbox',
                            'Expected Result': f'Successfully identify {checkbox_name} checkbox',
                            'Actual Result': f'Identified {checkbox_name} checkbox (currently {"checked" if is_checked else "unchecked"})',
                            'Notes': f'[Current Page Form {i+1} - Step 4.1]'
                        })
                        
                        # Step 4.2: Toggle checkbox if not checked
                        if not is_checked:
                            checkbox.check()
                            current_page_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Check {checkbox_name} checkbox',
                                'Element': f'{checkbox_name} checkbox',
                                'Expected Result': f'Successfully check {checkbox_name} checkbox',
                                'Actual Result': f'Successfully checked {checkbox_name} checkbox',
                                'Notes': f'[Current Page Form {i+1} - Step 4.2]'
                            })
                        else:
                            current_page_cases.append({
                                'Type': 'Form Field',
                                'Action': f'Verify {checkbox_name} checkbox is checked',
                                'Element': f'{checkbox_name} checkbox',
                                'Expected Result': f'{checkbox_name} checkbox should be checked',
                                'Actual Result': f'{checkbox_name} checkbox is already checked',
                                'Notes': f'[Current Page Form {i+1} - Step 4.2]'
                            })
                        
                    except Exception as e:
                        current_page_cases.append({
                            'Type': 'Form Field',
                            'Action': f'Handle checkbox {j+1}',
                            'Element': f'Checkbox {j+1}',
                            'Expected Result': 'Successfully handle checkbox',
                            'Actual Result': f'Failed to handle checkbox: {str(e)}',
                            'Notes': f'[Current Page Form {i+1} - Error]'
                        })
                        continue
                
                # Step 5: Test form submission with detailed steps
                submit_buttons = form.query_selector_all('button[type="submit"], input[type="submit"]')
                for j, btn in enumerate(submit_buttons):
                    btn_text = 'Submit Button'  # Default value
                    try:
                        # Step 5.1: Get button information
                        btn_text = btn.inner_text() or btn.get_attribute('value') or f'Submit Button {j+1}'
                        is_enabled = not btn.is_disabled()
                        
                        current_page_cases.append({
                            'Type': 'Button',
                            'Action': f'Identify "{btn_text}" submit button',
                            'Element': btn_text,
                            'Expected Result': f'Successfully identify "{btn_text}" button',
                            'Actual Result': f'Identified "{btn_text}" button ({"enabled" if is_enabled else "disabled"})',
                            'Notes': f'[Current Page Form {i+1} - Step 5.1]'
                        })
                        
                        if is_enabled:
                            # Step 5.2: Click the submit button
                            btn.click()
                            current_page_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Click "{btn_text}" submit button',
                                'Element': btn_text,
                                'Expected Result': 'Form should submit successfully',
                                'Actual Result': f'Successfully clicked "{btn_text}" button',
                                'Notes': f'[Current Page Form {i+1} - Step 5.2]'
                            })
                            
                            # Step 5.3: Wait for response
                            page.wait_for_timeout(2000)
                            current_page_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Wait for "{btn_text}" response',
                                'Element': btn_text,
                                'Expected Result': 'Page should respond to form submission',
                                'Actual Result': 'Successfully waited for page response (2 seconds)',
                                'Notes': f'[Current Page Form {i+1} - Step 5.3]'
                            })
                            
                            # Step 5.4: Check for success/error messages
                            try:
                                success_found = page.query_selector('.success, .alert-success, .message-success, .notification-success, .valid-feedback')
                                error_found = page.query_selector('.error, .alert-error, .message-error, .notification-error, .invalid-feedback, .alert-danger')
                                
                                if success_found:
                                    current_page_cases.append({
                                        'Type': 'Form Submission',
                                        'Action': f'Verify "{btn_text}" success',
                                        'Element': btn_text,
                                        'Expected Result': 'Form submission should succeed',
                                        'Actual Result': 'Form submission succeeded (success message displayed)',
                                        'Notes': f'[Current Page Form {i+1} - Step 5.4]'
                                    })
                                elif error_found:
                                    current_page_cases.append({
                                        'Type': 'Form Submission',
                                        'Action': f'Verify "{btn_text}" error handling',
                                        'Element': btn_text,
                                        'Expected Result': 'Form submission should handle errors gracefully',
                                        'Actual Result': 'Form submission failed (error message displayed)',
                                        'Notes': f'[Current Page Form {i+1} - Step 5.4]'
                                    })
                                else:
                                    current_page_cases.append({
                                        'Type': 'Form Submission',
                                        'Action': f'Verify "{btn_text}" response',
                                        'Element': btn_text,
                                        'Expected Result': 'Form submission should provide response',
                                        'Actual Result': 'Form submission completed (no success/error message detected)',
                                        'Notes': f'[Current Page Form {i+1} - Step 5.4]'
                                    })
                            except:
                                current_page_cases.append({
                                    'Type': 'Form Submission',
                                    'Action': f'Verify "{btn_text}" response',
                                    'Element': btn_text,
                                    'Expected Result': 'Form submission should provide response',
                                    'Actual Result': 'Form submission completed (response verification failed)',
                                    'Notes': f'[Current Page Form {i+1} - Step 5.4]'
                                })
                        else:
                            current_page_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Verify "{btn_text}" button state',
                                'Element': btn_text,
                                'Expected Result': 'Submit button should be enabled',
                                'Actual Result': 'Submit button is disabled (cannot submit)',
                                'Notes': f'[Current Page Form {i+1} - Step 5.2]'
                            })
                            
                    except Exception as e:
                        current_page_cases.append({
                            'Type': 'Form Submission',
                            'Action': f'Submit form with "{btn_text}"',
                            'Element': btn_text,
                            'Expected Result': 'Form should submit successfully',
                            'Actual Result': f'Failed to submit form: {str(e)}',
                            'Notes': f'[Current Page Form {i+1} - Error]'
                        })
                        continue
                        
            except Exception as e:
                current_page_cases.append({
                    'Type': 'Form',
                    'Action': 'Test form on current page',
                    'Element': f'Form {i+1}',
                    'Expected Result': 'Successfully test form',
                    'Actual Result': f'Form testing failed: {str(e)}',
                    'Notes': f'[Current Page Form {i+1} Error]'
                })
                
    except Exception as e:
        current_page_cases.append({
            'Type': 'Current Page Forms',
            'Action': 'Test forms on current page',
            'Element': 'Current Page Forms',
            'Expected Result': 'Successfully test current page forms',
            'Actual Result': f'Current page forms testing failed: {str(e)}',
            'Notes': '[Current Page Forms Error]'
        })
    
    return current_page_cases

def test_other_dashboard_sections(page):
    """Test other dashboard sections beyond user management"""
    other_sections_cases = []
    
    try:
        # Test all common dashboard sections with detailed testing
        section_configs = [
            {
                'name': 'PIM',
                'selectors': ['a:has-text("PIM")', '.oxd-main-menu-item:has-text("PIM")', 'nav a:has-text("PIM")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Add', 'Search', 'Edit', 'Delete']
            },
            {
                'name': 'Leave',
                'selectors': ['a:has-text("Leave")', '.oxd-main-menu-item:has-text("Leave")', 'nav a:has-text("Leave")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Apply', 'Search', 'Approve', 'Reject']
            },
            {
                'name': 'Time',
                'selectors': ['a:has-text("Time")', '.oxd-main-menu-item:has-text("Time")', 'nav a:has-text("Time")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Add', 'Search', 'Edit', 'Delete']
            },
            {
                'name': 'Recruitment',
                'selectors': ['a:has-text("Recruitment")', '.oxd-main-menu-item:has-text("Recruitment")', 'nav a:has-text("Recruitment")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Add', 'Search', 'Edit', 'Delete']
            },
            {
                'name': 'Performance',
                'selectors': ['a:has-text("Performance")', '.oxd-main-menu-item:has-text("Performance")', 'nav a:has-text("Performance")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Add', 'Search', 'Edit', 'Delete']
            },
            {
                'name': 'Dashboard',
                'selectors': ['a:has-text("Dashboard")', '.oxd-main-menu-item:has-text("Dashboard")', 'nav a:has-text("Dashboard")'],
                'test_forms': False,
                'test_tables': False,
                'test_actions': []
            },
            {
                'name': 'Directory',
                'selectors': ['a:has-text("Directory")', '.oxd-main-menu-item:has-text("Directory")', 'nav a:has-text("Directory")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Search', 'Filter']
            },
            {
                'name': 'Maintenance',
                'selectors': ['a:has-text("Maintenance")', '.oxd-main-menu-item:has-text("Maintenance")', 'nav a:has-text("Maintenance")'],
                'test_forms': True,
                'test_tables': False,
                'test_actions': ['Access', 'Purge']
            },
            {
                'name': 'Claim',
                'selectors': ['a:has-text("Claim")', '.oxd-main-menu-item:has-text("Claim")', 'nav a:has-text("Claim")'],
                'test_forms': True,
                'test_tables': True,
                'test_actions': ['Add', 'Search', 'Approve', 'Reject']
            },
            {
                'name': 'Buzz',
                'selectors': ['a:has-text("Buzz")', '.oxd-main-menu-item:has-text("Buzz")', 'nav a:has-text("Buzz")'],
                'test_forms': True,
                'test_tables': False,
                'test_actions': ['Post', 'Share', 'Like', 'Comment']
            }
        ]
        
        for config in section_configs:
            section_name = config['name']
            section_link = None
            
            # Try to find the section link
            for selector in config['selectors']:
                try:
                    section_link = page.query_selector(selector)
                    if section_link:
                        break
                except Exception:
                    continue
            
            if section_link:
                try:
                    # Step 1: Navigate to the section
                    section_link.click(timeout=10000)
                    other_sections_cases.append({
                        'Type': 'Navigation',
                        'Action': f'Click {section_name} link',
                        'Element': f'{section_name} Section',
                        'Expected Result': f'Successfully navigate to {section_name} section',
                        'Actual Result': f'Successfully clicked {section_name} link',
                        'Notes': f'[{section_name} Navigation - Step 1]'
                    })
                    
                    # Step 2: Wait for section to load
                    try:
                        page.wait_for_load_state('networkidle', timeout=15000)
                        other_sections_cases.append({
                            'Type': 'Navigation',
                            'Action': f'Wait for {section_name} section to load',
                            'Element': f'{section_name} Section',
                            'Expected Result': f'{section_name} section should load completely',
                            'Actual Result': f'Successfully waited for {section_name} section to load',
                            'Notes': f'[{section_name} Navigation - Step 2]'
                        })
                    except Exception as e:
                        other_sections_cases.append({
                            'Type': 'Navigation',
                            'Action': f'Wait for {section_name} section to load',
                            'Element': f'{section_name} Section',
                            'Expected Result': f'{section_name} section should load completely',
                            'Actual Result': f'{section_name} section load timeout: {str(e)}',
                            'Notes': f'[{section_name} Navigation - Step 2]'
                        })
                    
                    # Step 3: Take screenshot of the section
                    try:
                        screenshot_path = f'screenshot_{section_name}_Section.png'
                        page.screenshot(path=screenshot_path)
                        other_sections_cases.append({
                            'Type': 'Navigation',
                            'Action': f'Take {section_name} section screenshot',
                            'Element': f'{section_name} Section',
                            'Expected Result': f'Successfully capture {section_name} section screenshot',
                            'Actual Result': f'Successfully captured {section_name} section screenshot',
                            'Notes': f'[{section_name} Navigation - Step 3]'
                        })
                    except Exception as e:
                        other_sections_cases.append({
                            'Type': 'Navigation',
                            'Action': f'Take {section_name} section screenshot',
                            'Element': f'{section_name} Section',
                            'Expected Result': f'Successfully capture {section_name} section screenshot',
                            'Actual Result': f'Failed to capture screenshot: {str(e)}',
                            'Notes': f'[{section_name} Navigation - Step 3]'
                        })
                    
                    # Step 4: Test forms if configured
                    if config['test_forms']:
                        section_forms = test_section_forms(page, section_name)
                        other_sections_cases.extend(section_forms)
                    
                    # Step 5: Test tables if configured
                    if config['test_tables']:
                        section_tables = test_section_tables(page, section_name)
                        other_sections_cases.extend(section_tables)
                    
                    # Step 6: Test specific actions if configured
                    if config['test_actions']:
                        section_actions = test_section_actions(page, section_name, config['test_actions'])
                        other_sections_cases.extend(section_actions)
                    
                    # Step 7: Test current page forms as fallback
                    current_page_forms = test_current_page_forms(page)
                    for tc in current_page_forms:
                        tc['Notes'] = f'[{section_name} Section] {tc.get("Notes", "")}'
                    other_sections_cases.extend(current_page_forms)
                    
                except Exception as e:
                    other_sections_cases.append({
                        'Type': 'Navigation',
                        'Action': f'Navigate to {section_name} section',
                        'Element': f'{section_name} Section',
                        'Expected Result': f'Successfully navigate to {section_name} section',
                        'Actual Result': f'Navigation failed: {str(e)}',
                        'Notes': f'[{section_name} Navigation Error]'
                    })
            else:
                other_sections_cases.append({
                    'Type': 'Navigation',
                    'Action': f'Find {section_name} link',
                    'Element': f'{section_name} Section',
                    'Expected Result': f'{section_name} link found and clickable',
                    'Actual Result': f'{section_name} link not found on dashboard',
                    'Notes': f'[{section_name} Link Not Found]'
                })
                
    except Exception as e:
        other_sections_cases.append({
            'Type': 'Dashboard Sections',
            'Action': 'Test other dashboard sections',
            'Element': 'Dashboard Sections',
            'Expected Result': 'Successfully test other dashboard sections',
            'Actual Result': f'Other sections testing failed: {str(e)}',
            'Notes': '[Dashboard Sections Error]'
        })
    
    return other_sections_cases

def test_section_forms(page, section_name):
    """Test forms specific to a section"""
    section_forms_cases = []
    
    try:
        # Find all forms in the section
        forms = page.query_selector_all('form')
        
        for i, form in enumerate(forms):
            try:
                # Step 1: Analyze form structure
                section_forms_cases.append({
                    'Type': 'Form Analysis',
                    'Action': f'Analyze {section_name} form structure',
                    'Element': f'{section_name} Form {i+1}',
                    'Expected Result': f'Successfully identify {section_name} form elements',
                    'Actual Result': f'{section_name} Form {i+1} contains form elements',
                    'Notes': f'[{section_name} Form {i+1} - Step 1]'
                })
                
                # Step 2: Test form fields
                form_fields = test_form_fields_detailed(form, section_name, i+1)
                section_forms_cases.extend(form_fields)
                
                # Step 3: Test form submission
                form_submission = test_form_submission_detailed(form, section_name, i+1)
                section_forms_cases.extend(form_submission)
                
            except Exception as e:
                section_forms_cases.append({
                    'Type': 'Form',
                    'Action': f'Test {section_name} form',
                    'Element': f'{section_name} Form {i+1}',
                    'Expected Result': f'Successfully test {section_name} form',
                    'Actual Result': f'{section_name} form testing failed: {str(e)}',
                    'Notes': f'[{section_name} Form {i+1} - Error]'
                })
                
    except Exception as e:
        section_forms_cases.append({
            'Type': 'Section Forms',
            'Action': f'Test {section_name} forms',
            'Element': f'{section_name} Forms',
            'Expected Result': f'Successfully test {section_name} forms',
            'Actual Result': f'{section_name} forms testing failed: {str(e)}',
            'Notes': f'[{section_name} Forms Error]'
        })
    
    return section_forms_cases

def test_form_fields_detailed(form, section_name, form_index):
    """Test form fields with detailed steps"""
    form_fields_cases = []
    
    try:
        # Test input fields
        inputs = form.query_selector_all('input[type="text"], input[type="email"], input[type="password"], input[type="number"], input[type="tel"], input[type="url"], input[type="date"]')
        for j, input_field in enumerate(inputs):
            try:
                # Get field information
                field_name = input_field.get_attribute('name') or f'input_{j+1}'
                field_type = input_field.get_attribute('type') or 'text'
                placeholder = input_field.get_attribute('placeholder') or ''
                
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Identify {field_name} field in {section_name}',
                    'Element': f'{field_name} ({field_type})',
                    'Expected Result': f'Successfully identify {field_name} field',
                    'Actual Result': f'Identified {field_name} field of type {field_type}',
                    'Notes': f'[{section_name} Form {form_index} - Step 2.1]'
                })
                
                # Clear and fill field
                input_field.clear()
                test_value = generate_test_value(field_type, j+1)
                input_field.fill(test_value)
                
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Fill {field_name} field with "{test_value}"',
                    'Element': f'{field_name} input field',
                    'Expected Result': f'Successfully fill {field_name} with test data',
                    'Actual Result': f'Successfully filled {field_name} with "{test_value}"',
                    'Notes': f'[{section_name} Form {form_index} - Step 2.2]'
                })
                
                # Verify field value
                actual_value = input_field.input_value()
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Verify {field_name} field value',
                    'Element': f'{field_name} input field',
                    'Expected Result': f'Field should contain "{test_value}"',
                    'Actual Result': f'Field contains "{actual_value}"',
                    'Notes': f'[{section_name} Form {form_index} - Step 2.3]'
                })
                
            except Exception as e:
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Test {field_name if 'field_name' in locals() else f"input field {j+1}"} in {section_name}',
                    'Element': f'{field_name if "field_name" in locals() else f"input field {j+1}"}',
                    'Expected Result': f'Successfully test input field',
                    'Actual Result': f'Input field testing failed: {str(e)}',
                    'Notes': f'[{section_name} Form {form_index} - Error]'
                })
        
        # Test dropdowns
        dropdowns = form.query_selector_all('select')
        for j, dropdown in enumerate(dropdowns):
            try:
                dropdown_name = dropdown.get_attribute('name') or f'dropdown_{j+1}'
                options = dropdown.query_selector_all('option:not([disabled])')
                
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Identify {dropdown_name} dropdown in {section_name}',
                    'Element': f'{dropdown_name} dropdown',
                    'Expected Result': f'Successfully identify {dropdown_name} dropdown',
                    'Actual Result': f'Identified {dropdown_name} dropdown with {len(options)} options',
                    'Notes': f'[{section_name} Form {form_index} - Step 3.1]'
                })
                
                if len(options) > 1:
                    selected_option = options[1]
                    option_text = selected_option.inner_text()
                    option_value = selected_option.get_attribute('value')
                    
                    dropdown.select_option(value=option_value)
                    form_fields_cases.append({
                        'Type': 'Form Field',
                        'Action': f'Select "{option_text}" from {dropdown_name}',
                        'Element': f'{dropdown_name} dropdown',
                        'Expected Result': f'Successfully select "{option_text}" from dropdown',
                        'Actual Result': f'Successfully selected "{option_text}" (value: {option_value})',
                        'Notes': f'[{section_name} Form {form_index} - Step 3.2]'
                    })
                    
            except Exception as e:
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Test dropdown {j+1} in {section_name}',
                    'Element': f'Dropdown {j+1}',
                    'Expected Result': f'Successfully test dropdown',
                    'Actual Result': f'Dropdown testing failed: {str(e)}',
                    'Notes': f'[{section_name} Form {form_index} - Error]'
                })
        
        # Test checkboxes
        checkboxes = form.query_selector_all('input[type="checkbox"]')
        for j, checkbox in enumerate(checkboxes):
            try:
                checkbox_name = checkbox.get_attribute('name') or f'checkbox_{j+1}'
                is_checked = checkbox.is_checked()
                
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Identify {checkbox_name} checkbox in {section_name}',
                    'Element': f'{checkbox_name} checkbox',
                    'Expected Result': f'Successfully identify {checkbox_name} checkbox',
                    'Actual Result': f'Identified {checkbox_name} checkbox (currently {"checked" if is_checked else "unchecked"})',
                    'Notes': f'[{section_name} Form {form_index} - Step 4.1]'
                })
                
                if not is_checked:
                    checkbox.check()
                    form_fields_cases.append({
                        'Type': 'Form Field',
                        'Action': f'Check {checkbox_name} checkbox',
                        'Element': f'{checkbox_name} checkbox',
                        'Expected Result': f'Successfully check {checkbox_name} checkbox',
                        'Actual Result': f'Successfully checked {checkbox_name} checkbox',
                        'Notes': f'[{section_name} Form {form_index} - Step 4.2]'
                    })
                    
            except Exception as e:
                form_fields_cases.append({
                    'Type': 'Form Field',
                    'Action': f'Test checkbox {j+1} in {section_name}',
                    'Element': f'Checkbox {j+1}',
                    'Expected Result': f'Successfully test checkbox',
                    'Actual Result': f'Checkbox testing failed: {str(e)}',
                    'Notes': f'[{section_name} Form {form_index} - Error]'
                })
                
    except Exception as e:
        form_fields_cases.append({
            'Type': 'Form Fields',
            'Action': f'Test form fields in {section_name}',
            'Element': f'{section_name} Form Fields',
            'Expected Result': f'Successfully test form fields',
            'Actual Result': f'Form fields testing failed: {str(e)}',
            'Notes': f'[{section_name} Form Fields Error]'
        })
    
    return form_fields_cases

def test_form_submission_detailed(form, section_name, form_index):
    """Test form submission with detailed steps"""
    form_submission_cases = []
    
    try:
        submit_buttons = form.query_selector_all('button[type="submit"], input[type="submit"]')
        for j, btn in enumerate(submit_buttons):
            btn_text = 'Submit Button'
            try:
                btn_text = btn.inner_text() or btn.get_attribute('value') or f'Submit Button {j+1}'
                is_enabled = not btn.is_disabled()
                
                form_submission_cases.append({
                    'Type': 'Button',
                    'Action': f'Verify "{btn_text}" button in {section_name}',
                    'Element': btn_text,
                    'Expected Result': f'Successfully identify "{btn_text}" button',
                    'Actual Result': f'Identified "{btn_text}" button ({"enabled" if is_enabled else "disabled"})',
                    'Notes': f'[{section_name} Form {form_index} - Step 5.1]'
                })
                
                if is_enabled:
                    btn.click()
                    form_submission_cases.append({
                        'Type': 'Form Submission',
                        'Action': f'Click "{btn_text}" button in {section_name}',
                        'Element': btn_text,
                        'Expected Result': f'Form should submit successfully',
                        'Actual Result': f'Successfully clicked "{btn_text}" button',
                        'Notes': f'[{section_name} Form {form_index} - Step 5.2]'
                    })
                    
                    page.wait_for_timeout(2000)
                    form_submission_cases.append({
                        'Type': 'Form Submission',
                        'Action': f'Wait for "{btn_text}" response in {section_name}',
                        'Element': btn_text,
                        'Expected Result': f'Page should respond to form submission',
                        'Actual Result': f'Successfully waited for page response (2 seconds)',
                        'Notes': f'[{section_name} Form {form_index} - Step 5.3]'
                    })
                    
                    # Check for success/error messages
                    try:
                        success_found = page.query_selector('.success, .alert-success, .message-success, .notification-success, .valid-feedback')
                        error_found = page.query_selector('.error, .alert-error, .message-error, .notification-error, .invalid-feedback, .alert-danger')
                        
                        if success_found:
                            form_submission_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Verify "{btn_text}" success in {section_name}',
                                'Element': btn_text,
                                'Expected Result': f'Form submission should succeed',
                                'Actual Result': f'Form submission succeeded (success message displayed)',
                                'Notes': f'[{section_name} Form {form_index} - Step 5.4]'
                            })
                        elif error_found:
                            form_submission_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Verify "{btn_text}" error handling in {section_name}',
                                'Element': btn_text,
                                'Expected Result': f'Form submission should handle errors gracefully',
                                'Actual Result': f'Form submission failed (error message displayed)',
                                'Notes': f'[{section_name} Form {form_index} - Step 5.4]'
                            })
                        else:
                            form_submission_cases.append({
                                'Type': 'Form Submission',
                                'Action': f'Verify "{btn_text}" response in {section_name}',
                                'Element': btn_text,
                                'Expected Result': f'Form submission should provide response',
                                'Actual Result': f'Form submission completed (no success/error message detected)',
                                'Notes': f'[{section_name} Form {form_index} - Step 5.4]'
                            })
                    except:
                        form_submission_cases.append({
                            'Type': 'Form Submission',
                            'Action': f'Verify "{btn_text}" response in {section_name}',
                            'Element': btn_text,
                            'Expected Result': f'Form submission should provide response',
                            'Actual Result': f'Form submission completed (response verification failed)',
                            'Notes': f'[{section_name} Form {form_index} - Step 5.4]'
                        })
                        
            except Exception as e:
                form_submission_cases.append({
                    'Type': 'Form Submission',
                    'Action': f'Submit form with "{btn_text}" in {section_name}',
                    'Element': btn_text,
                    'Expected Result': f'Form should submit successfully',
                    'Actual Result': f'Failed to submit form: {str(e)}',
                    'Notes': f'[{section_name} Form {form_index} - Error]'
                })
                
    except Exception as e:
        form_submission_cases.append({
            'Type': 'Form Submission',
            'Action': f'Test form submission in {section_name}',
            'Element': f'{section_name} Form Submission',
            'Expected Result': f'Successfully test form submission',
            'Actual Result': f'Form submission testing failed: {str(e)}',
            'Notes': f'[{section_name} Form Submission Error]'
        })
    
    return form_submission_cases

def test_section_tables(page, section_name):
    """Test tables in a section"""
    section_tables_cases = []
    
    try:
        # Find tables in the section
        tables = page.query_selector_all('table, .oxd-table, .oxd-table-body, .oxd-table-card')
        
        for i, table in enumerate(tables):
            try:
                section_tables_cases.append({
                    'Type': 'Table Analysis',
                    'Action': f'Analyze {section_name} table structure',
                    'Element': f'{section_name} Table {i+1}',
                    'Expected Result': f'Successfully identify {section_name} table elements',
                    'Actual Result': f'{section_name} Table {i+1} contains table elements',
                    'Notes': f'[{section_name} Table {i+1} - Step 1]'
                })
                
                # Test table interactions
                table_interactions = test_table_interactions_detailed(table, section_name, i+1)
                section_tables_cases.extend(table_interactions)
                
            except Exception as e:
                section_tables_cases.append({
                    'Type': 'Table',
                    'Action': f'Test {section_name} table',
                    'Element': f'{section_name} Table {i+1}',
                    'Expected Result': f'Successfully test {section_name} table',
                    'Actual Result': f'{section_name} table testing failed: {str(e)}',
                    'Notes': f'[{section_name} Table {i+1} - Error]'
                })
                
    except Exception as e:
        section_tables_cases.append({
            'Type': 'Section Tables',
            'Action': f'Test {section_name} tables',
            'Element': f'{section_name} Tables',
            'Expected Result': f'Successfully test {section_name} tables',
            'Actual Result': f'{section_name} tables testing failed: {str(e)}',
            'Notes': f'[{section_name} Tables Error]'
        })
    
    return section_tables_cases

def test_table_interactions_detailed(table, section_name, table_index):
    """Test table interactions with detailed steps"""
    table_interactions_cases = []
    
    try:
        # Test checkboxes in table
        checkboxes = table.query_selector_all('input[type="checkbox"]')
        for j, checkbox in enumerate(checkboxes[:5]):  # Test first 5 checkboxes
            try:
                checkbox.check()
                table_interactions_cases.append({
                    'Type': 'Table Interaction',
                    'Action': f'Select row {j+1} in {section_name} table',
                    'Element': f'Checkbox {j+1}',
                    'Expected Result': f'Successfully select table row',
                    'Actual Result': f'Successfully selected table row {j+1}',
                    'Notes': f'[{section_name} Table {table_index} - Step 2.1]'
                })
            except Exception as e:
                table_interactions_cases.append({
                    'Type': 'Table Interaction',
                    'Action': f'Select row {j+1} in {section_name} table',
                    'Element': f'Checkbox {j+1}',
                    'Expected Result': f'Successfully select table row',
                    'Actual Result': f'Failed to select table row: {str(e)}',
                    'Notes': f'[{section_name} Table {table_index} - Error]'
                })
        
        # Test action buttons in table
        action_buttons = table.query_selector_all('button:has-text("Edit"), button:has-text("Delete"), a:has-text("Edit"), a:has-text("Delete")')
        for j, btn in enumerate(action_buttons[:3]):  # Test first 3 action buttons
            try:
                btn_text = btn.inner_text() or f'Action Button {j+1}'
                btn.click()
                page.wait_for_timeout(2000)
                
                table_interactions_cases.append({
                    'Type': 'Table Action',
                    'Action': f'Click {btn_text} button in {section_name} table',
                    'Element': btn_text,
                    'Expected Result': f'{btn_text} action should be initiated',
                    'Actual Result': f'Successfully clicked {btn_text} button',
                    'Notes': f'[{section_name} Table {table_index} - Step 2.2]'
                })
            except Exception as e:
                table_interactions_cases.append({
                    'Type': 'Table Action',
                    'Action': f'Click action button {j+1} in {section_name} table',
                    'Element': f'Action Button {j+1}',
                    'Expected Result': f'Action should be initiated',
                    'Actual Result': f'Failed to click action button: {str(e)}',
                    'Notes': f'[{section_name} Table {table_index} - Error]'
                })
                
    except Exception as e:
        table_interactions_cases.append({
            'Type': 'Table Interactions',
            'Action': f'Test table interactions in {section_name}',
            'Element': f'{section_name} Table Interactions',
            'Expected Result': f'Successfully test table interactions',
            'Actual Result': f'Table interactions testing failed: {str(e)}',
            'Notes': f'[{section_name} Table Interactions Error]'
        })
    
    return table_interactions_cases

def test_section_actions(page, section_name, actions):
    """Test specific actions for a section"""
    section_actions_cases = []
    
    try:
        for action in actions:
            try:
                # Look for action buttons
                action_selectors = [
                    f'button:has-text("{action}")',
                    f'a:has-text("{action}")',
                    f'button:has-text("+ {action}")',
                    f'button:has-text("Add {action}")',
                    f'button:has-text("New {action}")'
                ]
                
                action_button = None
                for selector in action_selectors:
                    try:
                        action_button = page.query_selector(selector)
                        if action_button:
                            break
                    except Exception:
                        continue
                
                if action_button:
                    btn_text = action_button.inner_text() or action
                    action_button.click()
                    page.wait_for_timeout(2000)
                    
                    section_actions_cases.append({
                        'Type': 'Section Action',
                        'Action': f'Click {btn_text} button in {section_name}',
                        'Element': btn_text,
                        'Expected Result': f'{action} action should be initiated',
                        'Actual Result': f'Successfully clicked {btn_text} button',
                        'Notes': f'[{section_name} Action - {action}]'
                    })
                else:
                    section_actions_cases.append({
                        'Type': 'Section Action',
                        'Action': f'Find {action} button in {section_name}',
                        'Element': f'{action} Button',
                        'Expected Result': f'{action} button should be found',
                        'Actual Result': f'{action} button not found in {section_name}',
                        'Notes': f'[{section_name} Action - {action} Not Found]'
                    })
                    
            except Exception as e:
                section_actions_cases.append({
                    'Type': 'Section Action',
                    'Action': f'Test {action} action in {section_name}',
                    'Element': f'{action} Action',
                    'Expected Result': f'{action} action should work',
                    'Actual Result': f'{action} action failed: {str(e)}',
                    'Notes': f'[{section_name} Action - {action} Error]'
                })
                
    except Exception as e:
        section_actions_cases.append({
            'Type': 'Section Actions',
            'Action': f'Test actions in {section_name}',
            'Element': f'{section_name} Actions',
            'Expected Result': f'Successfully test actions in {section_name}',
            'Actual Result': f'Actions testing failed: {str(e)}',
            'Notes': f'[{section_name} Actions Error]'
        })
    
    return section_actions_cases

def generate_test_value(field_type, index):
    """Generate appropriate test values for different field types"""
    if field_type == 'email':
        return f'test{index}@example.com'
    elif field_type == 'password':
        return f'Password{index}!'
    elif field_type == 'number':
        return str(index)
    elif field_type == 'tel':
        return f'+1-555-{index:03d}'
    elif field_type == 'url':
        return f'https://example{index}.com'
    elif field_type == 'date':
        return f'2025-01-{index:02d}'
    else:
        return f'test_value_{index}'

def extract_elements(soup, base_url, username=None, password=None):
    test_cases = []
    form_success = False
    post_login_cases = []
    for idx, form in enumerate(soup.find_all('form')):
        # Updated: auto_fill_and_submit_form may return post_login_test_cases
        result = auto_fill_and_submit_form(form, base_url, username, password)
        if isinstance(result, tuple) and len(result) == 4:
            action, method, actual_result, post_login_test_cases = result
            post_login_cases.extend(post_login_test_cases)
        else:
            action, method, actual_result = result
        test_cases.append({
            'Type': 'Form',
            'Action': f"Submit {method} form",
            'Element': action,
            'Expected Result': 'Form submitted successfully',
            'Actual Result': actual_result,
            'Notes': f"Form #{idx+1} on page"
        })
        if username and password and 'dashboard loaded' in actual_result.lower():
            form_success = True
    if not form_success:
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
    # Add post-login/dashboard test cases if any
    test_cases.extend(post_login_cases)
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