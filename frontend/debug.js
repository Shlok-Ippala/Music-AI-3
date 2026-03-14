import puppeteer from 'puppeteer';
import { spawn } from 'child_process';
import fetch from 'node-fetch';

(async () => {
  console.log("Starting vite preview...");
  const server = spawn('npx', ['vite', 'preview', '--port', '4173'], { stdio: 'pipe' });
  
  await new Promise(r => setTimeout(r, 3000)); // wait for server to start

  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  
  page.on('console', msg => {
    console.log(`[BROWSER CONSOLE] ${msg.type().toUpperCase()}: ${msg.text()}`);
  });
  
  page.on('pageerror', error => {
    console.log(`[BROWSER ERROR] ${error.message}`);
  });

  try {
    console.log("Navigating to http://localhost:4173 ...");
    await page.goto('http://localhost:4173', { waitUntil: 'networkidle0' });
    const html = await page.content();
    console.log(`[SUCCESS] Page loaded. HTML length: ${html.length}`);
    if (html.length < 500) {
      console.log(`[HTML BODY]: ${html}`);
    }
  } catch (err) {
    console.error(`[NAVIGATION ERROR] ${err.message}`);
  }
  
  await browser.close();
  server.kill();
})();
