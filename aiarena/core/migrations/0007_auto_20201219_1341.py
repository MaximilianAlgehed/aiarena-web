# Generated by Django 3.0.8 on 2020-12-19 03:11

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_auto_20201219_0841"),
    ]

    operations = [
        migrations.RenameField(
            model_name="competitionparticipation",
            old_name="season",
            new_name="competition",
        ),
        migrations.RenameField(
            model_name="round",
            old_name="season",
            new_name="competition",
        ),
        migrations.AlterField(
            model_name="competitionbotmatchupstats",
            name="bot",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="competition_matchup_stats",
                to="core.CompetitionParticipation",
            ),
        ),
    ]
