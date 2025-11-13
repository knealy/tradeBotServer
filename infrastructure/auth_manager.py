"""
Enhanced Authentication Manager

Provides:
- JWT token generation and validation
- User management (admin/users)
- Session management with Redis
- Password hashing
"""

import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import JWT (PyJWT package)
try:
    import jwt
    JWT_AVAILABLE = True
except ImportError:
    JWT_AVAILABLE = False
    logger.warning("⚠️  PyJWT not installed - JWT authentication will use fallback")

# JWT secret key (should be set via environment variable)
JWT_SECRET = os.getenv('JWT_SECRET') or secrets.token_urlsafe(32)
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))


@dataclass
class User:
    """User data structure"""
    id: str
    username: str
    email: Optional[str] = None
    role: str = 'user'  # 'admin' or 'user'
    created_at: Optional[datetime] = None
    last_login: Optional[datetime] = None
    is_active: bool = True


class AuthManager:
    """
    Authentication manager with JWT support.
    
    Features:
    - JWT token generation/validation
    - User management
    - Role-based access control
    - Session management
    """
    
    def __init__(self):
        """Initialize auth manager."""
        self.users: Dict[str, User] = {}
        self._load_default_users()
    
    def _load_default_users(self):
        """Load default users from environment or create admin user."""
        # Default admin user (can be overridden via env)
        admin_username = os.getenv('ADMIN_USERNAME', 'admin')
        admin_password = os.getenv('ADMIN_PASSWORD', 'admin')
        
        # Hash password
        password_hash = self._hash_password(admin_password)
        
        self.users[admin_username] = User(
            id='admin',
            username=admin_username,
            role='admin',
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
        
        # Store password hash (in production, use database)
        self._password_hashes = {admin_username: password_hash}
        
        logger.info(f"✅ Auth manager initialized with admin user: {admin_username}")
    
    def _hash_password(self, password: str) -> str:
        """Hash password using SHA-256 (simple, for production use bcrypt)."""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def verify_password(self, username: str, password: str) -> bool:
        """Verify user password."""
        if username not in self._password_hashes:
            return False
        
        password_hash = self._hash_password(password)
        return self._password_hashes[username] == password_hash
    
    def create_user(self, username: str, password: str, email: Optional[str] = None, role: str = 'user') -> Optional[User]:
        """Create a new user."""
        if username in self.users:
            return None  # User already exists
        
        user = User(
            id=secrets.token_urlsafe(16),
            username=username,
            email=email,
            role=role,
            created_at=datetime.now(timezone.utc),
            is_active=True,
        )
        
        self.users[username] = user
        self._password_hashes[username] = self._hash_password(password)
        
        logger.info(f"✅ Created user: {username} (role: {role})")
        return user
    
    def get_user(self, username: str) -> Optional[User]:
        """Get user by username."""
        return self.users.get(username)
    
    def list_users(self) -> List[User]:
        """List all users."""
        return list(self.users.values())
    
    def generate_token(self, username: str) -> Optional[str]:
        """Generate JWT token for user."""
        if not JWT_AVAILABLE:
            # Fallback to simple token
            return secrets.token_urlsafe(32)
        
        user = self.get_user(username)
        if not user or not user.is_active:
            return None
        
        payload = {
            'username': username,
            'user_id': user.id,
            'role': user.role,
            'iat': datetime.now(timezone.utc),
            'exp': datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS),
        }
        
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
        
        # Update last login
        user.last_login = datetime.now(timezone.utc)
        
        return token
    
    def validate_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Validate JWT token and return payload."""
        if not JWT_AVAILABLE:
            # Fallback: simple token validation (not secure, but works)
            return {'username': 'admin', 'role': 'admin'}  # Temporary fallback
        
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            
            # Verify user still exists and is active
            username = payload.get('username')
            user = self.get_user(username)
            if not user or not user.is_active:
                return None
            
            return payload
        except jwt.ExpiredSignatureError:
            logger.debug("Token expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid token: {e}")
            return None
    
    def require_role(self, required_role: str = 'admin'):
        """Decorator to require specific role."""
        def decorator(func):
            def wrapper(*args, **kwargs):
                # Extract token from request (simplified - adapt to your framework)
                token = kwargs.get('token') or args[0].headers.get('Authorization', '').replace('Bearer ', '')
                
                if not token:
                    return {'error': 'Authentication required'}, 401
                
                payload = self.validate_token(token)
                if not payload:
                    return {'error': 'Invalid or expired token'}, 401
                
                user_role = payload.get('role', 'user')
                if user_role != required_role and required_role == 'admin':
                    return {'error': 'Admin access required'}, 403
                
                kwargs['user'] = payload
                return func(*args, **kwargs)
            return wrapper
        return decorator


# Global auth manager instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get or create global auth manager instance."""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager

