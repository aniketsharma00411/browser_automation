from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
import os
from openai import OpenAI
import json
import time
import base64
from io import BytesIO
import datetime
import websockets
import asyncio
import subprocess
import psutil
import signal
import aiohttp
import platform
import os.path

from src.id_manager import IdManager

load_dotenv()


class BrowserAgent:
    def __init__(self, proxy_config: Optional[Dict[str, str]] = None, extensions: Optional[List[str]] = None):
        """
        Initialize the browser agent with optional proxy and extension configurations.

        Args:
            proxy_config: Dictionary containing proxy settings (e.g., {'server': 'http://proxy:port'})
            extensions: List of paths to Chrome extensions to load
        """

        # Initialize OpenAI client
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.browser_process = None
        self.ws = None
        self.debug_port = None
        self.id_manager = IdManager()
        self.proxy_config = proxy_config
        self.extensions = extensions or []
        self.system = platform.system().lower()
        self.chat_history = []

    def _get_chrome_path(self) -> str:
        """Get the path to Chrome executable based on OS"""
        if self.system == "darwin":  # macOS
            return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif self.system == "linux":
            # Try common Linux Chrome paths
            possible_paths = [
                "/usr/bin/google-chrome",
                "/usr/bin/google-chrome-stable",
                "/usr/bin/chromium",
                "/usr/bin/chromium-browser"
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    return path
            return "google-chrome"  # Fallback to PATH
        else:  # Windows
            possible_paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.expandvars(
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    return path
            return "chrome"  # Fallback to PATH

    def _get_user_data_dir(self) -> str:
        """Get the appropriate user data directory based on OS"""
        if self.system == "darwin":  # macOS
            return "/tmp/chrome-automation"
        elif self.system == "linux":
            return "/tmp/chrome-automation"
        else:  # Windows
            return os.path.join(os.getenv("TEMP", "C:\\temp"), "chrome-automation")

    async def start(self):
        """Start a native browser instance with remote debugging enabled"""
        self.debug_port = 9222  # Default Chrome debugging port

        chrome_path = self._get_chrome_path()
        chrome_args = [
            chrome_path,
            f'--remote-debugging-port={self.debug_port}',
            '--no-first-run',
            '--no-default-browser-check',
            f'--user-data-dir={self._get_user_data_dir()}',
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-site-isolation-trials',
            '--disable-web-security',
            '--window-size=1920,1080',
            '--remote-allow-origins=*',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu'
        ]

        # Add proxy configuration if provided
        if self.proxy_config:
            if 'server' in self.proxy_config:
                chrome_args.append(
                    f'--proxy-server={self.proxy_config["server"]}')
            if 'username' in self.proxy_config and 'password' in self.proxy_config:
                chrome_args.append(f'--proxy-auth-scheme=basic')
                chrome_args.append(
                    f'--proxy-auth-data={self.proxy_config["username"]}:{self.proxy_config["password"]}')

        # Add extensions if provided
        if self.extensions:
            chrome_args.append('--enable-extensions')
            extension_paths = ','.join(path for path in self.extensions)
            chrome_args.append(
                f'--disable-extensions-except={extension_paths}')

        print(f"Starting Chrome with arguments: {' '.join(chrome_args)}")
        self.browser_process = subprocess.Popen(chrome_args)

        # Wait for browser to start and connect to debugging protocol
        await asyncio.sleep(2)  # Give browser time to start
        await self._connect_to_browser()

    async def _connect_to_browser(self):
        """Connect to browser's debugging protocol"""
        try:
            # First try to connect to the version endpoint using HTTP
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(f'http://localhost:{self.debug_port}/json/version') as response:
                        if response.status == 200:
                            data = await response.json()
                            ws_url = data['webSocketDebuggerUrl']
                            print(f"Got WebSocket URL: {ws_url}")
                        else:
                            raise Exception(
                                f"Failed to get WebSocket URL. Status: {response.status}")
                except Exception as e:
                    print(
                        f"Failed to connect to Chrome debugging endpoint: {e}")
                    raise

            # Connect to WebSocket
            self.ws = await websockets.connect(ws_url)
            print(f"Connected to Chrome at {ws_url}")

            # Get list of targets first
            async with aiohttp.ClientSession() as session:
                async with session.get(f'http://localhost:{self.debug_port}/json/list') as response:
                    if response.status == 200:
                        data = await response.json()
                        print(f"Available targets: {data}")
                        # Find the first page target
                        for target in data:
                            if target.get('type') == 'page':
                                ws_url = target['webSocketDebuggerUrl']
                                print(
                                    f"Found page WebSocket URL: {ws_url}")
                                # Connect to the page
                                self.ws = await websockets.connect(ws_url)
                                print(f"Connected to page at {ws_url}")
                                break

            # Enable necessary domains for the page
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.enable"
            }))
            response = await self.ws.recv()
            print(f"Page domain enabled: {response}")

            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.enable"
            }))
            response = await self.ws.recv()
            print(f"DOM domain enabled: {response}")

            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Input.enable"
            }))
            response = await self.ws.recv()
            print(f"Input domain enabled: {response}")

            # Set viewport size
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Emulation.setDeviceMetricsOverride",
                "params": {
                    "width": 1920,
                    "height": 1080,
                    "deviceScaleFactor": 1,
                    "mobile": False
                }
            }))
            response = await self.ws.recv()
            print(f"Viewport set: {response}")

            # Send initial handshake
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Browser.getVersion"
            }))
            response = await self.ws.recv()
            print(f"Browser version: {response}")

            # If proxy authentication is configured, handle it
            if self.proxy_config and 'username' in self.proxy_config and 'password' in self.proxy_config:
                await self._handle_proxy_auth()

        except Exception as e:
            print(f"Failed to connect to browser: {e}")
            raise

    async def _handle_proxy_auth(self):
        """Handle proxy authentication if configured"""
        try:
            # Enable the Security domain
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Security.enable"
            }))
            response = await self.ws.recv()
            print(f"Security domain enabled: {response}")

            # Set up proxy authentication
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Security.setOverrideCertificateErrors",
                "params": {
                    "override": True
                }
            }))
            response = await self.ws.recv()
            print(f"Certificate error override set: {response}")

        except Exception as e:
            print(f"Error handling proxy authentication: {e}")
            raise

    async def stop(self):
        """Stop the browser instance"""
        if self.ws:
            await self.ws.close()
        if self.browser_process:
            # Kill the browser process and its children
            parent = psutil.Process(self.browser_process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()

    async def wait_for_load(self):
        """Wait for page load to complete"""
        try:
            # Enable page load events
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.enable"
            }))
            response = await self.ws.recv()
            print(f"Page domain enable response in wait_for_load: {response}")

            # Wait for frameStoppedLoading event
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.frameStoppedLoading"
            }))
            response = await self.ws.recv()
            print(f"Frame stopped loading response: {response}")

            # Additional wait to ensure JavaScript is loaded
            await asyncio.sleep(1)
        except Exception as e:
            print(f"Error waiting for page load: {e}")

    async def goto(self, url: str):
        """Navigate to a URL"""
        try:
            print(f"Navigating to {url}...")

            # First ensure Page domain is enabled
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.enable"
            }))
            response = await self.ws.recv()
            print(f"Page domain enable response: {response}")

            # Get current page state
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.getNavigationHistory"
            }))
            response = await self.ws.recv()
            print(f"Current navigation state: {response}")

            # Navigate to URL
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.navigate",
                "params": {"url": url}
            }))
            response = await self.ws.recv()
            print(f"Navigation response: {response}")

            # Wait for navigation to complete
            await self.wait_for_load()
            print("Navigation completed")
        except Exception as e:
            print(f"Error during navigation: {e}")
            raise

    async def _wait_for_response(self, command_id: int, command_name: str):
        """Helper method to wait for a specific command response while handling events"""
        while True:
            response = await self.ws.recv()
            response_data = json.loads(response)

            # If this is an event (has 'method' field), skip it
            if 'method' in response_data:
                print(f"Skipping event: {response}")
                continue

            # If this is our response (has 'id' matching our request)
            if response_data.get('id') == command_id:
                print(f"{command_name} response: {response}")
                if 'error' in response_data:
                    raise Exception(
                        f"Failed in {command_name}: {response_data['error']}")
                return response_data

    async def _find_element(self, selector: str):
        """Helper method to find an element using a selector"""
        # Get the document root node
        await self.ws.send(json.dumps({
            "id": self.id_manager.next_id(),
            "method": "DOM.getDocument"
        }))
        response_data = await self._wait_for_response(self.id_manager._current_id, "Get document")
        root_node_id = response_data["result"]["root"]["nodeId"]

        # Find the element using DOM.querySelector
        await self.ws.send(json.dumps({
            "id": self.id_manager.next_id(),
            "method": "DOM.querySelector",
            "params": {
                "nodeId": root_node_id,
                "selector": selector
            }
        }))
        response_data = await self._wait_for_response(self.id_manager._current_id, "Query selector")
        node_id = response_data["result"]["nodeId"]
        if not node_id:
            raise Exception(f"Element not found with selector: {selector}")

        return node_id

    async def click(self, selector: str):
        """Click an element using the given selector"""
        try:
            print(f"Clicking element with selector: {selector}")

            # Find the element
            node_id = await self._find_element(selector)

            # Get element's box model to determine click coordinates
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.getBoxModel",
                "params": {
                    "nodeId": node_id
                }
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Box model")
            box_model = response_data

            # Calculate center point of the element
            content = box_model["result"]["model"]["content"]
            x = (content[0] + content[2]) / 2
            y = (content[1] + content[5]) / 2

            # Move mouse to element and click
            mouse_events = [
                {"type": "mouseMoved", "id": self.id_manager.next_id()},
                {"type": "mousePressed", "id": self.id_manager.next_id(),
                    "button": "left", "clickCount": 1},
                {"type": "mouseReleased", "id": self.id_manager.next_id(),
                    "button": "left", "clickCount": 1}
            ]

            for event in mouse_events:
                params = {
                    "type": event["type"],
                    "x": x,
                    "y": y
                }
                if "button" in event:
                    params["button"] = event["button"]
                if "clickCount" in event:
                    params["clickCount"] = event["clickCount"]

                await self.ws.send(json.dumps({
                    "id": event["id"],
                    "method": "Input.dispatchMouseEvent",
                    "params": params
                }))
                await self._wait_for_response(event["id"], f"Mouse {event['type']}")

                # Add small delay between events
                if event["type"] != "mouseReleased":
                    await asyncio.sleep(0.1)

            # Wait for navigation to start
            while True:
                response = await self.ws.recv()
                response_data = json.loads(response)

                if response_data.get("method") == "Page.frameStartedLoading":
                    print("Navigation started")
                    break
                if "id" in response_data:
                    print(f"Unexpected response: {response}")
                    break

            # Wait for navigation to complete
            await self.wait_for_load()
            print("Navigation completed")

        except Exception as e:
            print(f"Error clicking element: {e}")
            raise

    async def press_key(self, params: dict, press_type: str):
        # Send keyDown event
        await self.ws.send(json.dumps({
            "id": self.id_manager.next_id(),
            "method": "Input.dispatchKeyEvent",
            "params": {
                "type": press_type,
                **params
            }
        }))
        await self._wait_for_response(self.id_manager._current_id, f"Key press")

    async def keyboard_press(self, key: str):
        """Simulate pressing a key using Playwright-style API"""
        # Handle special keys
        key_mapping = {
            'Control': {'key': 'Control', 'windowsVirtualKeyCode': 17},
            'Enter': {'key': 'Enter', 'windowsVirtualKeyCode': 13},
            'Backspace': {'key': 'Backspace', 'windowsVirtualKeyCode': 8},
            'Delete': {'key': 'Delete', 'windowsVirtualKeyCode': 46},
            'Escape': {'key': 'Escape', 'windowsVirtualKeyCode': 27},
            'Tab': {'key': 'Tab', 'windowsVirtualKeyCode': 9},
            'Shift': {'key': 'Shift', 'windowsVirtualKeyCode': 16},
            'Alt': {'key': 'Alt', 'windowsVirtualKeyCode': 18},
            'Meta': {'key': 'Meta', 'windowsVirtualKeyCode': 91},
            'ArrowDown': {'key': 'ArrowDown', 'windowsVirtualKeyCode': 40},
            'ArrowUp': {'key': 'ArrowUp', 'windowsVirtualKeyCode': 38},
            'ArrowLeft': {'key': 'ArrowLeft', 'windowsVirtualKeyCode': 37},
            'ArrowRight': {'key': 'ArrowRight', 'windowsVirtualKeyCode': 39},
            'Home': {'key': 'Home', 'windowsVirtualKeyCode': 36},
            'End': {'key': 'End', 'windowsVirtualKeyCode': 35},
            'PageUp': {'key': 'PageUp', 'windowsVirtualKeyCode': 33},
            'PageDown': {'key': 'PageDown', 'windowsVirtualKeyCode': 34},
            'Insert': {'key': 'Insert', 'windowsVirtualKeyCode': 45},
            'F1': {'key': 'F1', 'windowsVirtualKeyCode': 112},
            'F2': {'key': 'F2', 'windowsVirtualKeyCode': 113},
            'F3': {'key': 'F3', 'windowsVirtualKeyCode': 114},
            'F4': {'key': 'F4', 'windowsVirtualKeyCode': 115},
            'F5': {'key': 'F5', 'windowsVirtualKeyCode': 116},
            'F6': {'key': 'F6', 'windowsVirtualKeyCode': 117},
            'F7': {'key': 'F7', 'windowsVirtualKeyCode': 118},
            'F8': {'key': 'F8', 'windowsVirtualKeyCode': 119},
            'F9': {'key': 'F9', 'windowsVirtualKeyCode': 120},
            'F10': {'key': 'F10', 'windowsVirtualKeyCode': 121},
            'F11': {'key': 'F11', 'windowsVirtualKeyCode': 122},
            'F12': {'key': 'F12', 'windowsVirtualKeyCode': 123}
        }

        # Get key parameters
        params = key_mapping.get(key, {'text': key})

        await self.press_key(params, "keyDown")
        await self.press_key(params, "keyUp")

        # Add a small delay between key events
        await asyncio.sleep(0.05)
        if key == 'Enter':
            await self.wait_for_load()

    async def fill(self, selector: str, text: str):
        """Fill text into an input element using the given selector"""
        try:
            print(f"Filling text into element with selector: {selector}")

            # Find the element
            node_id = await self._find_element(selector)

            # Focus the element
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.focus",
                "params": {
                    "nodeId": node_id
                }
            }))
            await self._wait_for_response(self.id_manager._current_id, "Focus element")

            # Type the new text
            for i, char in enumerate(text):
                await self.keyboard_press(char)
                await asyncio.sleep(0.05)

            print(f"Successfully filled text: {text}")

        except Exception as e:
            print(f"Error filling text: {e}")
            raise

    async def clear_text(self, selector: str):
        """Clear text from an input element using the given selector"""
        try:
            print(f"Clearing text from element with selector: {selector}")

            # Find the element
            node_id = await self._find_element(selector)

            # Focus the element
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.focus",
                "params": {
                    "nodeId": node_id
                }
            }))
            await self._wait_for_response(self.id_manager._current_id, "Focus element")

            # First ensure Runtime domain is enabled
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Runtime.enable"
            }))
            response = await self.ws.recv()
            print(f"Runtime enable response: {response}")

            # Construct JavaScript to clear the input value
            js_script = f"""
                (function() {{
                    const element = document.querySelector('{selector}');
                    if (!element) return false;
                    element.value = '';
                    // Trigger input event to ensure the change is registered
                    element.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    return true;
                }})()
            """

            # Send evaluation request
            cmd_id = self.id_manager.next_id()
            await self.ws.send(json.dumps({
                "id": cmd_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js_script,
                    "returnByValue": True
                }
            }))

            # Wait for the response
            while True:
                response = await self.ws.recv()
                response_data = json.loads(response)
                print(f"Received response: {response_data}")

                # Skip any event messages
                if 'method' in response_data:
                    continue

                # If this is our response
                if response_data.get('id') == cmd_id:
                    if "error" in response_data:
                        raise Exception(
                            f"Failed to clear text: {response_data['error']}")

                    result = response_data.get("result", {}).get(
                        "result", {}).get("value", False)
                    if not result:
                        raise Exception(
                            f"Element not found with selector: {selector}")

                    print("Successfully cleared text")
                    return

        except Exception as e:
            print(f"Error clearing text: {e}")
            raise

    async def screenshot(self, path: str = None):
        """Take a screenshot of the page"""
        try:
            print("Taking screenshot...")

            # Get the viewport size
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.getLayoutMetrics"
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Get layout metrics")
            metrics = response_data["result"]

            # Use CSS viewport for consistent sizing
            viewport = metrics["cssLayoutViewport"]

            # Always use viewport dimensions for consistent window size
            width = viewport["clientWidth"]
            height = viewport["clientHeight"]

            # Capture screenshot
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.captureScreenshot",
                "params": {
                    "format": "png",
                    "fromSurface": True,
                    "clip": {
                        "x": 0,
                        "y": 0,
                        "width": width,
                        "height": height,
                        "scale": 1.0
                    }
                }
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Capture screenshot")

            # Save to file if path is provided
            if path:
                # Decode base64 image data
                image_data = base64.b64decode(response_data["result"]["data"])
                with open(path, 'wb') as f:
                    f.write(image_data)
                print(f"Screenshot saved to {path}")

            return response_data["result"]["data"]

        except Exception as e:
            print(f"Error taking screenshot: {e}")
            raise

    async def url(self) -> str:
        """Get the current page URL"""
        try:
            print("Getting current URL...")

            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Page.getNavigationHistory"
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Get navigation history")

            # Get the current entry's URL
            current_entry = response_data["result"]["entries"][-1]
            url = current_entry["url"]
            print(f"Current URL: {url}")

            return url

        except Exception as e:
            print(f"Error getting URL: {e}")
            return "No URL found"

    async def title(self) -> str:
        """Get the current page title"""
        try:
            print("Getting page title...")

            # Get the document root node
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.getDocument"
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Get document")
            root_node_id = response_data["result"]["root"]["nodeId"]

            # Find the title element
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.querySelector",
                "params": {
                    "nodeId": root_node_id,
                    "selector": "title"
                }
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Query title element")
            title_node_id = response_data["result"]["nodeId"]

            if not title_node_id:
                raise Exception("Title element not found")

            # Get the title text
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "DOM.getOuterHTML",
                "params": {
                    "nodeId": title_node_id
                }
            }))
            response_data = await self._wait_for_response(self.id_manager._current_id, "Get title text")

            # Extract text from HTML
            title_html = response_data["result"]["outerHTML"]
            title = title_html.replace("<title>", "").replace("</title>", "")
            print(f"Page title: {title}")

            return title

        except Exception as e:
            print(f"Error getting title: {e}")
            return "No title found"

    async def get_page_info(self, extract_data: str | None = None) -> Dict[str, Any]:
        """Gather information about the current page state with screenshot."""
        try:
            # Get the current URL and title
            current_url = await self.url()
            title = await self.title()

            # Take a screenshot
            if not os.path.exists("screenshots"):
                os.makedirs("screenshots")
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"screenshots/screenshot_{timestamp}.png"
            screenshot_base64 = await self.screenshot(path=filename)

            data = None
            if extract_data:
                extract_result = await self.extract(extract_data)
                if extract_result["status"] == "success":
                    data = extract_result["data"][:50]

            return {
                "url": current_url,
                "title": title,
                "screenshot": screenshot_base64,
                "data": data
            }
        except Exception as e:
            return {"error": str(e)}

    async def execute_command(self, command: str) -> Dict[str, Any]:
        try:
            page_info = None
            run_agent = True
            max_iterations = 10
            iteration = 0

            while iteration < max_iterations and run_agent:
                iteration += 1

                # Prepare messages for OpenAI
                messages = [
                    {
                        "role": "system",
                        "content": """
                        You are a browser automation assistant. Parse the user's command into a json format. You are allowed to perform an action and then request to see the page information before proceeding. Set the needs_page_info to true if you need to see the page information after performing the action before proceeding to take the next action. Your response should be parsable using Python's json.loads function.
                        The json should have the following fields:
                        - action: The action to perform. The action should be one of the following:
                            - no_action: Do nothing. Use it if you only need to see the page information screenshot.
                            - navigate: Navigate to the url, where the "url" is the key in the json. The value of the key will be the input to the goto function of playwright.
                            - click: Click on the element, where the "selector" is the key in the json. The value of the key will be the input to the click function of playwright.
                            - type: Type the text, where the "selector" and "text" are the keys in the json. The values of the keys will be the input to the fill function of playwright.
                            - search: Search for the query. For this we will have the following keys in the json. 
                                - url: The url to navigate to.  The value of the key will be the input to the goto function of playwright.
                                - selector: Click on the element. The value of the key will be the input to the fill function of playwright.
                                - text: The text to type. The value of the key will be the input to the fill function of playwright.
                                - submit_selector: The selector for the login/submit button
                            - login: Perform login action. For this we will have the following keys in the json:
                                - url: The login page URL
                                - username_selector: The selector for the username/email field
                                - password_selector: The selector for the password field
                                - username: The username/email to enter
                                - password: The password to enter
                                - submit_selector: The selector for the login/submit button
                        - needs_page_info: A boolean indicating whether you need to see the current page state before proceeding. If the user command has been executed, set this to false. Only set this to true if the user command has not been executed, confirm this from the screenshot.
                        - extract_data: If needs_page_info is true, then you need to extract data from the page. Give a natural language description of the data you need to extract, this will be passed to another AI agent to extract the data based on the selector and attribute. This is required if needs_page_info is true.

                        Extra Information:
                        - If you are trying to input something at google.com then the selector is 'textarea[name="q"]'
                        """
                    }
                ]

                # Add the user's command
                messages.append({"role": "user", "content": command})

                # Keep last 10 messages for context
                for msg in self.chat_history[-10:]:
                    # Ensure content is a string
                    content = msg.get("content", None)
                    if isinstance(content, dict):
                        content = json.dumps(content)
                    elif isinstance(content, list):
                        content = json.dumps(content)

                    if content is not None:
                        messages.append({
                            "role": msg["role"],
                            "content": content
                        })

                # Add page information if provided
                if page_info:
                    messages.append({
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Current URL: {page_info['url']}\nCurrent Title: {page_info['title']}",
                                "extract_data": page_info["data"]
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{page_info['screenshot']}",
                                    "detail": "low"
                                }
                            }
                        ]
                    })

                # Get response from OpenAI
                response = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages
                )
                self.chat_history.append({
                    "role": "assistant",
                    "content": response.choices[0].message.content.strip()
                })

                # Parse the response content
                try:
                    content = response.choices[0].message.content.strip()
                    print("Raw response:", content)  # Debug print
                    parsed_command = json.loads(content)
                except json.JSONDecodeError as e:
                    print(f"JSON parsing error: {str(e)}")
                    print(f"Invalid JSON content: {content}")
                    self.chat_history.append({
                        "role": "user",
                        "content": f"Failed to parse AI response: {str(e)}"
                    })
                    continue

                # Execute the parsed command
                try:
                    if parsed_command["action"] == "navigate":
                        await self.goto(parsed_command["url"])
                    elif parsed_command["action"] == "click":
                        await self.click(parsed_command["selector"])
                    elif parsed_command["action"] == "type":
                        await self.fill(parsed_command["selector"], parsed_command["text"])
                    elif parsed_command["action"] == "search":
                        await self.goto(parsed_command["url"])
                        await self.fill(parsed_command["selector"], parsed_command["text"])

                        # Try both Enter key and submit button if available
                        try:
                            await self.click(parsed_command["submit_selector"])
                        except Exception as e:
                            await self.keyboard_press('Enter')
                    elif parsed_command["action"] == "login":
                        await self.goto(parsed_command["url"])
                        await self.fill(parsed_command["username_selector"], parsed_command["username"])
                        await self.fill(parsed_command["password_selector"], parsed_command["password"])
                        try:
                            await self.click(parsed_command["submit_selector"])
                        except Exception as e:
                            await self.keyboard_press('Enter')
                    await self.keyboard_press('Enter')

                except Exception as e:
                    print(f"Command execution error: {str(e)}")
                    self.chat_history.append({
                        "role": "user",
                        "content": f"Failed to execute command: {str(e)}"
                    })
                    continue

                # Check if we need page information
                if parsed_command["needs_page_info"]:
                    page_info = await self.get_page_info(parsed_command.get("extract_data", None))
                else:
                    run_agent = False

            return {
                "status": "success",
                "message": f"Executed command: {command}",
                "chat_history": self.chat_history
            }

        except Exception as e:
            print(f"General error: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def extract_data(self, selector: str, attribute: str | None = None, multiple: bool = False):
        try:
            print(
                f"Extracting data with selector: {selector}, attribute: {attribute}, multiple: {multiple}")

            # First ensure Runtime domain is enabled
            await self.ws.send(json.dumps({
                "id": self.id_manager.next_id(),
                "method": "Runtime.enable"
            }))
            response = await self.ws.recv()
            print(f"Runtime enable response: {response}")

            # Construct JavaScript expression
            if multiple:
                js_script = f"""
                    JSON.stringify(Array.from(document.querySelectorAll('{selector}')).map(el => {{
                        return {f"el.getAttribute('{attribute}')" if attribute else "el.textContent"};
                    }}))
                """
            else:
                js_script = f"""
                    JSON.stringify((() => {{
                        const el = document.querySelector('{selector}');
                        if (!el) return null;
                        return {f"el.getAttribute('{attribute}')" if attribute else "el.textContent"};
                    }})())
                """

            print(f"Executing JavaScript: {js_script}")

            # Send evaluation request
            cmd_id = self.id_manager.next_id()
            await self.ws.send(json.dumps({
                "id": cmd_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": js_script,
                    "returnByValue": True
                }
            }))

            # Wait for the specific response with our command ID
            while True:
                response = await self.ws.recv()
                response_data = json.loads(response)
                print(f"Received response: {response_data}")

                # Skip any event messages
                if 'method' in response_data:
                    continue

                # If this is our response
                if response_data.get('id') == cmd_id:
                    if "error" in response_data:
                        return {"status": "error", "message": str(response_data["error"])}

                    try:
                        result = json.loads(
                            response_data["result"]["result"]["value"])
                        if result is None:
                            return {"status": "error", "message": "Element not found"}
                        return {"status": "success", "data": result}
                    except (KeyError, json.JSONDecodeError) as e:
                        print(f"Error parsing result: {e}")
                        return {"status": "error", "message": f"Failed to parse result: {str(e)}"}
                    break

        except Exception as e:
            print(f"Error in extract_data: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def extract(self, query: str) -> Dict[str, Any]:
        try:
            # Get current page information
            page_info = await self.get_page_info()

            # Prepare messages for OpenAI
            messages = [
                {
                    "role": "system",
                    "content": """
                    You are a web scraping assistant. Your task is to determine the best CSS selector and attribute to extract data from a webpage based on a natural language query. Your response should be parsable using Python's json.loads function without any additional formatting.
                    
                    The json should have the following fields:
                    - selector: The CSS selector to use
                    - attribute: The attribute to extract (optional, if not provided, text content will be extracted)
                    - multiple: Boolean indicating whether to get all matching elements or just the first one
                    - explanation: A brief explanation of why these parameters were chosen
                    """
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Current URL: {page_info['url']}\nCurrent Title: {page_info['title']}\n\nQuery: {query}"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{page_info['screenshot']}",
                                "detail": "low"
                            }
                        }
                    ]
                }
            ]

            # Get response from OpenAI
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages
            )

            # Parse the response
            try:
                print(response.choices[0].message.content.strip())
                extraction_params = json.loads(
                    response.choices[0].message.content.strip())
            except json.JSONDecodeError as e:
                return {
                    "status": "error",
                    "message": f"Failed to parse AI response: {str(e)}"
                }

            # Extract data using the determined parameters
            result = await self.extract_data(
                selector=extraction_params["selector"],
                attribute=extraction_params.get("attribute"),
                multiple=extraction_params.get("multiple", False)
            )

            # Add explanation to the result
            if result["status"] == "success":
                result["explanation"] = extraction_params["explanation"]

            return result

        except Exception as e:
            print(f"Error in extract: {str(e)}")
            return {"status": "error", "message": str(e)}
