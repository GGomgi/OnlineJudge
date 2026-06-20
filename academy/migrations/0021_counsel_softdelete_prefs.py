from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0020_school_intl"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="counselinglog", name="counsel_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="counselinglog", name="is_hidden",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="counselinglog", name="edited_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="counselinglog", name="prev_summary",
                            field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="counselinglog", name="edited_by",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="lead", name="is_hidden",
                            field=models.BooleanField(default=False)),
        migrations.AddField(model_name="lead", name="deleted_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="lead", name="deleted_by",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="academyprofile", name="prefs",
                            field=models.TextField(blank=True, default="")),
    ]
