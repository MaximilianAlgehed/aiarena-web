# Generated by Django 2.1.7 on 2019-08-18 00:51

import aiarena.core.models
import aiarena.core.storage
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0040_auto_20190816_1124'),
    ]

    operations = [
        migrations.AddField(
            model_name='statsbots',
            name='elo_graph',
            field=models.FileField(blank=True, null=True, storage=aiarena.core.storage.OverwriteStorage(), upload_to=aiarena.core.models.elo_graph_upload_to),
        ),
    ]
