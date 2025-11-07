from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("recommender", "0003_rename_recommende_user_id_9fb003_idx_recommender_user_id_bd4071_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="playlistgenerationstat",
            name="completion_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="playlistgenerationstat",
            name="prompt_tokens",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="playlistgenerationstat",
            name="total_tokens",
            field=models.BigIntegerField(default=0),
        ),
    ]
