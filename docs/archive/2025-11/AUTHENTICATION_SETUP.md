# Bill Review App - Role-Based Authentication System

## Overview

The Bill Review app now has a complete role-based authentication system with three user roles:

### User Roles

1. **System_Admins**
   - Full access to all features
   - User management (create/edit/delete users)
   - All configuration settings
   - All bill processing features
   - Access to `/users` management page

2. **UBI_Admins**
   - UBI mapping and configuration
   - Bill review and approval
   - Generate reports
   - Limited config access
   - Pages: Home, UBI, Mappings, Review, Config, Track, Debug

3. **Utility_APs**
   - View bills assigned to them
   - Submit invoices
   - Basic reporting
   - Read-only access to most features
   - Pages: Home, Review, Invoices, Track

## Initial Setup

### Default Admin Account

An initial admin account has been created:

```
Email: admin@jrkanalytics.com
Password: ChangeMe123!
```

**IMPORTANT:** You will be prompted to change this password on first login.

## User Management (System Admins Only)

### Access User Management
1. Login as a System Admin
2. Navigate to `/users` or click "Users" in the navigation menu

### Create New Users
1. Click "Create User" button
2. Fill in:
   - Email address (will be the username)
   - Full name
   - Role (System_Admins, UBI_Admins, or Utility_APs)
   - Temporary password (minimum 8 characters)
3. Click "Create User"
4. New user will be created with `must_change_password` flag
5. User will be required to change password on first login

### Reset User Passwords
1. Go to `/users` page
2. Click "Reset" button next to user
3. Enter new temporary password
4. User will be required to change password on next login

### Enable/Disable Users
1. Go to `/users` page
2. Click "Enable" or "Disable" button next to user
3. Disabled users cannot log in

## Password Requirements

- Minimum length: 8 characters
- Must be changed on first login (for new users or after password reset)
- Hashed with bcrypt (12 rounds)

## DynamoDB Tables

### jrk-bill-review-users

**Primary Key:** `user_id` (String) - Email address

**Attributes:**
- `password_hash` (String) - Bcrypt hashed password
- `role` (String) - System_Admins, UBI_Admins, or Utility_APs
- `full_name` (String) - User's full name
- `enabled` (Boolean) - Account status
- `must_change_password` (Boolean) - Forces password change on next login
- `created_utc` (String) - ISO 8601 timestamp
- `created_by` (String) - Admin who created the user
- `last_login_utc` (String) - Last successful login timestamp
- `password_changed_utc` (String) - Last password change timestamp

**Global Secondary Index:** `role-index`
- Partition Key: `role`
- Allows querying users by role

## API Endpoints

### Authentication
- `POST /login` - Login with email and password
- `POST /logout` - Logout and clear session
- `GET /change-password` - Show change password form
- `POST /change-password` - Update password

### User Management (System Admins Only)
- `GET /users` - User management page
- `GET /api/users` - List all users (JSON)
- `POST /api/users` - Create new user
- `POST /api/users/{user_id}/disable` - Disable user account
- `POST /api/users/{user_id}/enable` - Enable user account
- `POST /api/users/{user_id}/reset-password` - Reset user password

## Permission System

The `auth.py` module provides functions to check permissions:

```python
import auth

# Check if user has specific permission
if auth.has_permission(user_role, "bills:write"):
    # Allow action

# Check if user can access a page
if auth.can_access_page(user_role, "/ubi"):
    # Show page

# Get user data
user_data = auth.get_user(user_email)
if user_data and user_data.get("enabled"):
    role = user_data.get("role")
```

### Permission Format

Permissions use the format `resource:action`:
- `bills:read`, `bills:write`, `bills:submit`, `bills:review`, `bills:approve`
- `ubi:read`, `ubi:write`, `ubi:config`
- `config:read`, `config:write`, `config:write:ubi`
- `reports:read`, `reports:generate`
- `invoices:read`, `invoices:process`

System Admins have the wildcard permission `*` which grants all permissions.

## IAM Permissions

The app requires DynamoDB access to the users table:

```json
{
  "Sid": "DDB",
  "Effect": "Allow",
  "Action": [
    "dynamodb:DescribeTable",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:Query",
    "dynamodb:Scan"
  ],
  "Resource": [
    "arn:aws:dynamodb:us-east-1:*:table/jrk-bill-review-users",
    "arn:aws:dynamodb:us-east-1:*:table/jrk-bill-review-users/index/*"
  ]
}
```

This has been added to the `jrk-bill-review-instance-role` IAM role.

## Security Best Practices

1. **Change Default Password:** Immediately change the default admin password
2. **Strong Passwords:** Enforce minimum 8 character passwords (consider increasing to 12+)
3. **Regular Audits:** Review user list regularly, disable unused accounts
4. **Password Rotation:** Encourage users to change passwords every 90 days
5. **Monitor Login Activity:** Check `last_login_utc` for suspicious activity
6. **Principle of Least Privilege:** Assign minimum role needed for each user

## Troubleshooting

### User Can't Login
1. Verify user exists in DynamoDB table
2. Check `enabled` field is `true`
3. Verify password was typed correctly
4. Check CloudWatch logs for authentication errors

### Permission Denied Errors
1. Verify user's role in DynamoDB
2. Check `auth.ROLES` in `auth.py` for role permissions
3. Review CloudWatch logs for permission check failures

### Password Reset Not Working
1. Verify admin user has `System_Admins` role
2. Check network connectivity to DynamoDB
3. Review CloudWatch logs for errors

## Future Enhancements

Consider implementing:
1. Multi-factor authentication (MFA)
2. Password complexity requirements (uppercase, numbers, symbols)
3. Account lockout after failed login attempts
4. Session timeout/auto-logout
5. Audit log for all user management actions
6. Email notifications for password resets
7. Self-service password reset via email
8. Integration with Azure AD / SAML / OAuth2

## Files Modified/Created

### New Files
- `auth.py` - Authentication and authorization module
- `templates/change_password.html` - Password change form
- `templates/users.html` - User management page
- `infra/create_users_table.ps1` - DynamoDB table creation script
- `AUTHENTICATION_SETUP.md` - This documentation

### Modified Files
- `main.py` - Added user management endpoints, updated login flow
- `infra/fix_debug_permissions.json` - Added users table permissions
- `requirements.txt` - Already had bcrypt dependency

## Support

For issues or questions about the authentication system:
1. Check CloudWatch logs: `/aws/apprunner/jrk-bill-review-service/*`
2. Review DynamoDB table: `jrk-bill-review-users`
3. Test API endpoints with curl or Postman
4. Contact system administrator

---

**Last Updated:** 2025-11-09
**Version:** 1.0
