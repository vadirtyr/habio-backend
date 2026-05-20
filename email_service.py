import os
import resend

resend.api_key = os.getenv("RESEND_API_KEY")

EMAIL_FROM = os.getenv("EMAIL_FROM")


async def send_password_reset_email(to_email, reset_link):
    resend.Emails.send({
        "from": f"OurOrbit <{EMAIL_FROM}>",
        "to": [to_email],
        "subject": "Reset your password",
        "html": f"""
        <h2>Reset Your Password</h2>

        <p>We received a request to reset your password.</p>

        <p>
            <a href="{reset_link}">
                Reset Password
            </a>
        </p>

        <p>This link expires in 1 hour.</p>

        <p>If you did not request this, you can ignore this email.</p>
        """,
    })