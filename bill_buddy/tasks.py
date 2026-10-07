import smtplib

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


def _send_account_email(subject, message, recipient):
    sent_count = send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=False,
    )
    if sent_count != 1:
        raise RuntimeError(f'Account email was not accepted for {recipient}.')


@shared_task(
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=True,
    retry_kwargs={'max_retries': 5},
)
def send_verification_email_task(recipient, first_name, verify_url):
    subject = 'Verify Your Email - Bill Buddy'
    message = (
        f'Hi {first_name},\n\n'
        'Please verify your email by clicking the link below:\n\n'
        f'{verify_url}\n\n'
        'If you did not register, please ignore this email.\n\n'
        'Thanks,\nBill Buddy Team'
    )
    _send_account_email(subject, message, recipient)


@shared_task(
    autoretry_for=(OSError, smtplib.SMTPException),
    retry_backoff=True,
    retry_kwargs={'max_retries': 5},
)
def send_password_reset_email_task(recipient, first_name, reset_url):
    subject = 'Reset Your Password - Bill Buddy'
    message = (
        f'Hi {first_name},\n\n'
        'You requested a password reset. Click the link below to reset your password:\n\n'
        f'{reset_url}\n\n'
        "If you didn't request this, please ignore this email.\n\n"
        'Thanks,\nBill Buddy Team'
    )
    _send_account_email(subject, message, recipient)
