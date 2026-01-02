"""
Authentication Module - Handles broker authentication and token management.

This module abstracts authentication logic, making it easy to swap
authentication methods or add new brokers.
"""

import os
import logging
import json
import base64
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime, timezone, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Try to import jwt (PyJWT), fallback to base64 if not available
try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    jwt = None

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


class AuthManager:
    """
    Manages authentication and session tokens.
    
    Handles token refresh, expiration checking, and authentication requests.
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        base_url: str = "https://api.topstepx.com"
    ):
        """
        Initialize authentication manager.
        
        Args:
            api_key: API key (or from environment)
            username: Username (or from environment)
            base_url: Base API URL
        """
        self.api_key = api_key or os.getenv('PROJECT_X_API_KEY') or os.getenv('TOPSETPX_API_KEY')
        self.username = username or os.getenv('PROJECT_X_USERNAME') or os.getenv('TOPSETPX_USERNAME')
        self.base_url = base_url
        
        # Try to load JWT token from environment (useful for Railway deployment)
        env_jwt = os.getenv('JWT_TOKEN')
        if env_jwt:
            self.session_token = env_jwt
            # Parse JWT to extract expiration time
            try:
                if HAS_JWT and jwt:
                    decoded = jwt.decode(env_jwt, options={"verify_signature": False})
                    exp_timestamp = decoded.get('exp')
                    if exp_timestamp:
                        self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                        logger.info(f"Loaded JWT from environment (expires: {self.token_expiry})")
                    else:
                        self.token_expiry = None
                        logger.warning("JWT loaded from environment but has no expiration claim")
                else:
                    # Fallback to base64 decoding
                    parts = env_jwt.split('.')
                    if len(parts) >= 2:
                        payload = parts[1]
                        payload += '=' * (4 - len(payload) % 4)
                        decoded = json.loads(base64.urlsafe_b64decode(payload))
                        exp_timestamp = decoded.get('exp')
                        if exp_timestamp:
                            self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                            logger.info(f"Loaded JWT from environment (expires: {self.token_expiry})")
                        else:
                            self.token_expiry = None
            except Exception as parse_err:
                logger.warning(f"Failed to parse JWT from environment: {parse_err}")
                self.token_expiry = None
        else:
            self.session_token = None
            self.token_expiry = None
        
        # HTTP session for authentication requests
        self._http_session = self._create_http_session()
    
    def _create_http_session(self) -> requests.Session:
        """
        Create HTTP session with retry logic.
        
        Returns:
            Configured requests.Session
        """
        session = requests.Session()
        # Increased retries and backoff for 500 errors (server issues)
        # 5 retries with exponential backoff: 2, 4, 8, 16, 32 seconds
        retry_strategy = Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"],
            raise_on_status=False  # Don't raise on status, let us handle it
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session
    
    def _is_token_expired(self) -> bool:
        """
        Check if current token is expired.
        
        Returns:
            True if token is expired or missing
        """
        if not self.session_token:
            return True
        
        if not self.token_expiry:
            # If we don't know expiration, assume expired for safety
            return True
        
        # Add 5 minute buffer before actual expiration
        buffer = datetime.now(timezone.utc) + timedelta(minutes=5)
        return buffer >= self.token_expiry
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: int = 30
    ) -> Dict[str, Any]:
        """
        Make HTTP request to TopStepX API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint
            data: Request data (for POST requests)
            headers: Request headers
            timeout: Request timeout in seconds
            
        Returns:
            Response dictionary
        """
        url = f"{self.base_url}{endpoint}"
        request_headers = headers or {}
        
        # Add auth header if we have a token
        if self.session_token and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.session_token}"
        
        try:
            if method.upper() == "POST":
                # Remove None values (TopStepX rejects None)
                if data:
                    cleaned_data = {k: v for k, v in data.items() if v is not None}
                else:
                    cleaned_data = None
                
                response = self._http_session.post(
                    url,
                    json=cleaned_data,
                    headers=request_headers,
                    timeout=timeout
                )
            else:
                response = self._http_session.request(
                    method=method,
                    url=url,
                    headers=request_headers,
                    timeout=timeout
                )
            
            # Handle response
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as e:
                status_code = response.status_code
                # Include server response body (TopStepX often puts the real reason here)
                body_text = ""
                try:
                    body_text = (response.text or "").strip()
                except Exception:
                    body_text = ""

                error_msg = f"HTTP {status_code}: {str(e)}"
                if body_text:
                    # Keep it single-line for logs; preserve full body in return payload.
                    snippet = body_text.replace("\n", " ")
                    if len(snippet) > 600:
                        snippet = snippet[:600] + "…"
                    error_msg = f"{error_msg} | body: {snippet}"
                
                # Provide more context for 500 errors
                if status_code == 500:
                    logger.error(f"Server error (500) from {endpoint}")
                    logger.warning("⚠️  500 errors might indicate:")
                    logger.warning("   1. TopStepX server is temporarily unavailable")
                    logger.warning("   2. Token may have expired (will attempt refresh on retry)")
                    logger.warning("   3. Account settings issue (check 'Auto OCO Brackets' for bracket orders)")
                    # Try to get error message from response body
                    try:
                        error_body = response.json() if response.text else {}
                        if error_body:
                            logger.debug(f"Error response body: {error_body}")
                    except:
                        pass
                
                logger.error(error_msg)
                return {"error": error_msg, "status_code": status_code, "response_text": body_text}
            
            # Parse JSON response
            try:
                if not response.text.strip():
                    return {"success": True, "message": "Operation completed successfully"}
                json_response = response.json()
                # Remove None values from error fields to prevent false error logs
                if isinstance(json_response, dict) and "error" in json_response and json_response["error"] is None:
                    # Remove None error field to prevent false error detection
                    json_response = {k: v for k, v in json_response.items() if k != "error" or v is not None}
                return json_response
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON response: {e}")
                return {"error": f"Invalid JSON response: {e}"}
                
        except requests.exceptions.Timeout:
            error_msg = f"Request timed out after {timeout}s"
            logger.error(error_msg)
            return {"error": error_msg}
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Connection error: {str(e)}"
            logger.error(error_msg)
            return {"error": error_msg}
        except requests.exceptions.RetryError as e:
            # This happens when all retries are exhausted
            error_msg = f"Request failed after all retries: {str(e)}"
            logger.error(error_msg)
            # Check if it's a 500 error - might be token-related
            if "500" in str(e) or "too many 500" in str(e).lower():
                logger.warning("⚠️  Multiple 500 errors received. This might indicate:")
                logger.warning("   1. Server is temporarily unavailable")
                logger.warning("   2. Token may have expired (try refreshing)")
                logger.warning("   3. Account settings issue (check 'Auto OCO Brackets' setting)")
            return {"error": error_msg, "retry_exhausted": True}
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Request failed: {error_msg}")
            # Check for ResponseError from urllib3
            if "too many 500" in error_msg.lower() or "ResponseError" in str(type(e)):
                logger.warning("⚠️  Multiple 500 errors received. This might indicate:")
                logger.warning("   1. Server is temporarily unavailable")
                logger.warning("   2. Token may have expired (try refreshing)")
                logger.warning("   3. Account settings issue (check 'Auto OCO Brackets' setting)")
                return {"error": error_msg, "retry_exhausted": True, "status_code": 500}
            return {"error": error_msg}
    
    async def authenticate(self) -> bool:
        """
        Authenticate with the TopStepX API using username and API key.
        
        Returns:
            True if authentication successful, False otherwise
        """
        if not self.api_key or not self.username:
            logger.error("API key and username are required")
            return False
        
        try:
            logger.info("Authenticating with TopStepX API...")
            
            # Prepare login data (TopStepX uses userName and apiKey)
            login_data = {
                "userName": self.username,
                "apiKey": self.api_key
            }
            
            # Set headers for login request
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json"
            }
            
            # Make login request
            response = self._make_request("POST", "/api/Auth/loginKey", data=login_data, headers=headers)
            
            # Check for actual errors (not just presence of "error" key with None/empty value)
            # Only treat as error if error key exists AND has a truthy, non-None value
            if isinstance(response, dict):
                error_value = response.get("error")
                # Only log error if it's a real error (not None, not empty string)
                if error_value is not None and error_value:
                    logger.error(f"Authentication failed: {error_value}")
                    return False
            
            # Check if login was successful
            # TopStepX API might return token directly as string, or in a "token" field
            token = None
            if isinstance(response, str):
                # Response is a token string directly
                token = response
            elif isinstance(response, dict):
                # Response is a dict - check for token field
                token = response.get("token")
                # Also check if response itself is the token (some APIs return just the token)
                if not token and len(response) == 1 and "token" not in response:
                    # Might be a different structure
                    pass
            
            if token:
                self.session_token = token if isinstance(token, str) else str(token)
            else:
                # No token found - check if there's an error message
                error_msg = None
                if isinstance(response, dict):
                    # Check for error messages, but skip if value is None or empty string
                    error_msg = response.get("errorMessage") or response.get("message")
                    # Only use "error" field if it's a real error (not None, not empty, not string "None")
                    error_field = response.get("error")
                    if error_field and error_field != "None" and str(error_field).strip():
                        error_msg = error_field
                
                if error_msg and str(error_msg).strip() and str(error_msg) != "None":
                    logger.error(f"Authentication failed: {error_msg}")
                else:
                    logger.error("Authentication failed: No token received from API")
                return False
                
            # Parse JWT to extract expiration time
            try:
                if HAS_JWT and jwt:
                    # Decode without verification (we trust the server's token)
                    decoded = jwt.decode(self.session_token, options={"verify_signature": False})
                    exp_timestamp = decoded.get("exp")
                    if exp_timestamp:
                        self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                        logger.info(f"Token expires at: {self.token_expiry}")
                    else:
                        # Default to 30 minutes if no expiry in token
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                        logger.warning("No expiry in JWT, assuming 30 minute lifetime")
                else:
                    # Fall back to base64 decoding if PyJWT not available
                    raise ImportError("PyJWT not available")
            except (ImportError, AttributeError):
                # If PyJWT not installed, fall back to base64 decoding
                try:
                    # JWT format: header.payload.signature
                    parts = self.session_token.split('.')
                    if len(parts) >= 2:
                        # Decode payload (add padding if needed)
                        payload = parts[1]
                        payload += '=' * (4 - len(payload) % 4)
                        decoded = json.loads(base64.urlsafe_b64decode(payload))
                        exp_timestamp = decoded.get("exp")
                        if exp_timestamp:
                            self.token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
                            logger.info(f"Token expires at: {self.token_expiry}")
                        else:
                            self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    else:
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                except Exception as parse_err:
                    # If parsing fails, assume 30 minute lifetime
                    self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                    logger.warning(f"Failed to parse token expiry: {parse_err}, assuming 30 minute lifetime")
            except Exception as decode_err:
                # If decoding fails, assume 30 minute lifetime
                self.token_expiry = datetime.now(timezone.utc) + timedelta(minutes=30)
                logger.warning(f"Failed to decode token: {decode_err}, assuming 30 minute lifetime")
            
            # If we got here, token was successfully set and parsed
            logger.info(f"Successfully authenticated as: {self.username}")
            logger.info(f"Session token obtained: {self.session_token[:20]}...")
            return True
            
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            return False
    
    async def ensure_valid_token(self, force_refresh: bool = False) -> bool:
        """
        Ensure we have a valid token, refreshing if necessary.
        
        Args:
            force_refresh: If True, force token refresh even if token appears valid
        
        Returns:
            True if token is valid
            
        Raises:
            AuthenticationError: If token refresh fails
        """
        if not force_refresh and not self._is_token_expired():
            return True
        
        if force_refresh:
            logger.info("Force refreshing token...")
        else:
            logger.info("Token expired or missing, authenticating...")
        return await self.authenticate()
    
    def get_token(self) -> Optional[str]:
        """
        Get current session token.
        
        Returns:
            Session token or None if not authenticated
        """
        return self.session_token
    
    def get_auth_headers(self) -> dict:
        """
        Get authentication headers for API requests.
        
        Returns:
            Dictionary with Authorization header
        """
        if not self.session_token:
            return {}
        return {"Authorization": f"Bearer {self.session_token}"}
    
    async def list_accounts(self) -> List[Dict[str, Any]]:
        """
        List all active accounts for the authenticated user.
        
        Returns:
            List of account information dictionaries
        """
        try:
            # Ensure valid token before making request
            await self.ensure_valid_token()
            
            logger.info("Fetching active accounts from TopStepX API...")
            
            if not self.session_token:
                logger.error("No session token available. Please authenticate first.")
                return []
            
            # Make real API call to get accounts using session token
            headers = {
                "accept": "text/plain",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.session_token}"
            }
            
            # Search for active accounts
            search_data = {
                "onlyActiveAccounts": True
            }
            
            response = self._make_request("POST", "/api/Account/search", data=search_data, headers=headers)
            
            # If we get 401/403, try refreshing token and retry once
            if response.get("status_code") in (401, 403):
                logger.info("Token expired during request, refreshing...")
                if await self.authenticate():
                    # Retry the request with new token
                    headers["Authorization"] = f"Bearer {self.session_token}"
                    response = self._make_request("POST", "/api/Account/search", data=search_data, headers=headers)
            
            if "error" in response:
                logger.error(f"Failed to fetch accounts: {response['error']}")
                return []
            
            # Parse the response - adjust based on actual API response structure
            if isinstance(response, list):
                accounts = response
            elif isinstance(response, dict) and "accounts" in response:
                accounts = response["accounts"]
            elif isinstance(response, dict) and "data" in response:
                accounts = response["data"]
            elif isinstance(response, dict) and "result" in response:
                accounts = response["result"]
            else:
                logger.warning(f"Unexpected API response format: {response}")
                accounts = []
            
            # Normalize account data structure
            normalized_accounts = []
            for account in accounts:
                # Determine account type from name or other fields
                account_name = account.get("name") or account.get("accountName", "Unknown Account")
                account_type = "unknown"
                
                if "PRAC" in account_name.upper():
                    account_type = "practice"
                elif "50KTC" in account_name.upper() or "100KTC" in account_name.upper() or "150KTC" in account_name.upper():
                    account_type = "eval"
                elif "EXPRESS" in account_name.upper():
                    account_type = "funded"
                elif "EVAL" in account_name.upper():
                    account_type = "evaluation"
                
                normalized_account = {
                    "id": account.get("id") or account.get("accountId"),
                    "name": account_name,
                    "status": account.get("status", "active"),
                    "balance": account.get("balance", 0.0),
                    "currency": account.get("currency", "USD"),
                    "account_type": account_type
                }
                normalized_accounts.append(normalized_account)
            
            logger.info(f"Found {len(normalized_accounts)} active accounts")
            return normalized_accounts
            
        except Exception as e:
            logger.error(f"Failed to fetch accounts: {str(e)}")
            return []

