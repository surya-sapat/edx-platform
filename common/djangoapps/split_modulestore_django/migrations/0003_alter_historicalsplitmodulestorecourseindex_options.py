# Generated by Django 3.2.20 on 2023-08-08 09:44

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('split_modulestore_django', '0002_data_migration'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='historicalsplitmodulestorecourseindex',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical split modulestore course index', 'verbose_name_plural': 'historical Split modulestore course indexes'},
        ),
    ]
