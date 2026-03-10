from django.core.mail import send_mail
from django.urls import reverse
from django.conf import settings
from .models import PasswordResetToken, EmailVerificationToken
from django.core.signing import TimestampSigner, SignatureExpired, BadSignature

def send_verification_email(user, request):
    signer = TimestampSigner()
    token = signer.sign(user.email)

    # Invalidate previous tokens
    EmailVerificationToken.objects.filter(user=user).delete()

    # Save new token
    EmailVerificationToken.objects.create(user=user, token=token)

    verify_url = request.build_absolute_uri(
        reverse('email-verify') + f'?token={token}'
    )

    subject = 'Verify Your Email - Bill Buddy'
    message = f"""
    Hi {user.first_name},

    Please verify your email by clicking the link below:

    {verify_url}

    If you did not register, please ignore this email.

    Thanks,
    Bill Buddy Team
    """
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])



def send_password_reset_email(user, request):
    signer = TimestampSigner()
    token = signer.sign(user.email)

    # Invalidate previous tokens
    PasswordResetToken.objects.filter(user=user).delete()

    # Save new token
    PasswordResetToken.objects.create(user=user, token=token)

    # reset_url = request.build_absolute_uri(
    #     reverse('password-reset-confirm') + f'?token={token}'
    # )
    reset_url = f"{settings.FRONTEND_URL}/auth/reset-password?token={token}"


    subject = 'Reset Your Password - Bill Buddy'
    message = f"""
    Hi {user.first_name},

    You requested a password reset. Click the link below to reset your password:

    {reset_url}

    If you didn't request this, please ignore this email.

    Thanks,
    Bill Buddy Team
    """
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])


def send_settlement_reminder(user_email, group_name, amount_due):
    subject = f"Reminder: Settlement due in group {group_name}"
    message = f"Hi,\n\nYou have a pending settlement of {amount_due} in the group '{group_name}'. Please settle it at your earliest convenience.\n\nThanks!"
    send_mail(subject, message, None, [user_email])




# from django.core.signing import TimestampSigner
# from django.core.mail import send_mail
# from django.urls import reverse
# from django.conf import settings
# from .models import EmailVerificationToken, PasswordResetToken


# def send_verification_email(user):
#     signer = TimestampSigner()
#     token = signer.sign(user.email)

#     # Save token to DB for one-time use
#     EmailVerificationToken.objects.create(user=user, token=token)

#     # Frontend verification link
#     FRONTEND_URL = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
#     verify_url = f"{FRONTEND_URL}/verify-email?token={token}"

#     subject = 'Verify Your Email - Bill Buddy'
#     message = f"""
#     Hi {user.first_name},

#     Please verify your email by clicking the link below:

#     {verify_url}

#     If you did not register, please ignore this email.

#     Thanks,  
#     Bill Buddy Team
#     """

#     send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])


# def send_password_reset_email(user):
#     signer = TimestampSigner()
#     token = signer.sign(user.email)

#     # Save token to DB for one-time use
#     PasswordResetToken.objects.create(user=user, token=token)

#     # Frontend reset password link
#     FRONTEND_URL = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
#     reset_url = f"{FRONTEND_URL}/reset-password?token={token}"

#     subject = 'Reset Your Password - Bill Buddy'
#     message = f"""
#     Hi {user.first_name},

#     You requested a password reset. Click the link below to reset your password:

#     {reset_url}

#     If you didn't request this, please ignore this email.

#     Thanks,  
#     Bill Buddy Team
#     """

#     send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [user.email])
