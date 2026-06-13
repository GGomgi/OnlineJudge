from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0007_lead_purpose"),
    ]

    operations = [
        migrations.AddField(model_name="studentprofile", name="gender",
                            field=models.CharField(blank=True, default="", max_length=8)),
        migrations.AddField(model_name="studentprofile", name="zipcode",
                            field=models.CharField(blank=True, default="", max_length=16)),
        migrations.AddField(model_name="studentprofile", name="address_detail",
                            field=models.CharField(blank=True, default="", max_length=255)),
        migrations.AddField(model_name="studentprofile", name="consent_privacy",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="studentprofile", name="consent_guardian_name",
                            field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="studentprofile", name="consent_signature",
                            field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="studentprofile", name="consent_date",
                            field=models.DateField(blank=True, null=True)),
    ]
