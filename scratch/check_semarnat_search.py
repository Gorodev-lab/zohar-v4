import sys
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

sys.path.append(str(Path(__file__).parent.parent))
from scrapers.semarnat_downloader import make_chrome_driver

def check_search():
    download_dir = Path("scratch/downloads")
    download_dir.mkdir(parents=True, exist_ok=True)
    driver = make_chrome_driver(download_dir, headless=True)
    try:
        url = "https://app.semarnat.gob.mx/consulta-tramite/#/portal-consulta/consulta-tramite/"
        driver.get(url)
        print("Page loaded. URL:", driver.current_url)
        time.sleep(5)
        
        # Look for search input
        search_input = None
        for sel in [
            (By.CSS_SELECTOR, "input[placeholder*='bitácora']"),
            (By.CSS_SELECTOR, "input[placeholder*='clave']"),
            (By.CSS_SELECTOR, "input[type='text']"),
            (By.XPATH, "//input")
        ]:
            try:
                search_input = driver.find_element(*sel)
                print("Found search input using selector:", sel)
                break
            except Exception:
                pass
                
        if not search_input:
            print("Could not find search input")
            return
            
        # Click and type using JS to trigger angular input event
        driver.execute_script("arguments[0].click();", search_input)
        search_input.clear()
        search_input.send_keys("21PU2025H0155")
        driver.execute_script("arguments[0].dispatchEvent(new Event('input', { bubbles: true }));", search_input)
        print("Typed key and dispatched 'input' event")
        
        # Look for search button
        search_btn = None
        for btn_sel in [
            (By.XPATH, "//button[contains(., 'Buscar')]"),
            (By.XPATH, "//button[contains(text(), 'Buscar')]"),
            (By.CSS_SELECTOR, "button.btn-primary"),
            (By.CSS_SELECTOR, "button"),
            (By.XPATH, "//*[contains(text(), 'Buscar')]")
        ]:
            try:
                search_btn = driver.find_element(*btn_sel)
                print("Found search button using selector:", btn_sel)
                break
            except Exception:
                pass
                
        if not search_btn:
            print("Could not find search button")
            return
            
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_btn)
        time.sleep(0.3)
        driver.execute_script("arguments[0].click();", search_btn)
        print("Clicked search button via JS. Waiting for results...")
        time.sleep(8)
        
        print("Current URL after search:", driver.current_url)
        
        # Take a screenshot to visualize what's there
        screenshot_path = Path("scratch/search_result.png")
        driver.save_screenshot(str(screenshot_path))
        print("Screenshot saved to:", screenshot_path.resolve())
        
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print("Does body text contain clave '21PU2025H0155'?", "21PU2025H0155" in body_text)
        print("Does body text contain bitacora '09/MP-0586/12/25'?", "09/MP-0586/12/25" in body_text)
        
        # Print first 500 chars of body text to see what is on screen
        print("Body text snippet:")
        print(body_text[:1000])
        
    finally:
        driver.quit()

if __name__ == "__main__":
    check_search()
