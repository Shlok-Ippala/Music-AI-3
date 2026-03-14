import puppeteer from 'puppeteer';

(async () => {
  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  
  page.on('console', msg => {
    console.log(`[BROWSER CONSOLE] ${msg.type().toUpperCase()}: ${msg.text()}`);
  });
  
  page.on('pageerror', error => {
    console.log(`[BROWSER ERROR] ${error.message}`);
  });

  page.on('requestfailed', request => {
    console.log(`[REQUEST FAILED] ${request.url()} - ${request.failure().errorText}`);
  });

  try {
    console.log("Navigating to http://localhost:5173 ...");
    await page.goto('http://localhost:5173', { waitUntil: 'networkidle0', timeout: 15000 });
    const html = await page.content();
    console.log(`[SUCCESS] Page loaded. HTML length: ${html.length}`);
    await page.screenshot({ path: 'frontend-screencap.png', fullPage: true });
    console.log("[SCREENSHOT] Saved to frontend-screencap.png");
    if (html.length < 500) {
      console.log(`[HTML BODY]: ${html}`);
    }
  } catch (err) {
    console.error(`[NAVIGATION ERROR] ${err.message}`);
  }
  
  await browser.close();
})();
