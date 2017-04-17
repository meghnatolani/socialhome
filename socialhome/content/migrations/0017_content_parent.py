# -*- coding: utf-8 -*-
# Generated by Django 1.10.5 on 2017-04-08 08:34
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0016_populate_content_rendered'),
    ]

    operations = [
        migrations.AddField(
            model_name='content',
            name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='content.Content', verbose_name='Parent'),
        ),
    ]
