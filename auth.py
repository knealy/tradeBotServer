"""
Authentication module for dashboard access
Provides token-based authentication and rate limiting
"""

import os
import time
import hashlib
from typing import Optional, Dict, Any
from functools import wraps

# Rate limiting storage (in-memory for simplicity)
_rate_limits: Dict[str, Dict[str, Any]] = {}

def get_auth_token() -> Optional[str]:
    """Get the dashboard authentication token from environment variables"""
    return os.getenv('DASHBOARD_AUTH_TOKEN')

def validate_token(token: str) -> bool:
    """Validate the provided authentication token"""
    expected_token = get_auth_token()
    if not expected_token:
        return False
    return token == expected_token

def extract_token_from_request(headers: Dict[str, str], query_params: Dict[str, str] = None) -> Optional[str]:
    """Extract authentication token from request headers or query parameters"""
    # Check Authorization header (Bearer token)
    auth_header = headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    
    # Check query parameter
    if query_params and 'token' in query_params:
        return query_params['token']
    
    return None

def is_rate_limited(client_ip: str, endpoint: str, max_requests: int = 60, window_seconds: int = 60) -> bool:
    """Check if client is rate limited for a specific endpoint"""
    current_time = time.time()
    key = f"{client_ip}:{endpoint}"
    
    if key not in _rate_limits:
        _rate_limits[key] = {'requests': [], 'window_start': current_time}
    
    rate_data = _rate_limits[key]
    
    # Clean old requests outside the window
    rate_data['requests'] = [
        req_time for req_time in rate_data['requests'] 
        if current_time - req_time < window_seconds
    ]
    
    # Check if limit exceeded
    if len(rate_data['requests']) >= max_requests:
        return True
    
    # Add current request
    rate_data['requests'].append(current_time)
    return False

def require_auth(func):
    """Decorator to require authentication for API endpoints"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        # Extract client IP (simplified)
        client_ip = self.client_address[0] if hasattr(self, 'client_address') else 'unknown'
        
        # Check rate limiting
        if is_rate_limited(client_ip, self.path):
            self._send_response(429, {"error": "Rate limit exceeded"})
            return
        
        # Extract token
        headers = dict(self.headers) if hasattr(self, 'headers') else {}
        query_params = {}
        
        # Parse query string if present
        if '?' in self.path:
            path, query = self.path.split('?', 1)
            query_params = dict(param.split('=') for param in query.split('&') if '=' in param)
        
        token = extract_token_from_request(headers, query_params)
        
        if not token or not validate_token(token):
            self._send_response(401, {"error": "Authentication required"})
            return
        
        return func(self, *args, **kwargs)
    return wrapper

def get_cors_headers() -> Dict[str, str]:
    """Get CORS headers for Railway deployment"""
    return {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '86400'
    }
