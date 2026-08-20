import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('tenancy', '0001_initial'),
        ('revenue', '0002_rateband_ratetier_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='AgentPortfolio',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('effective_from', models.DateField(auto_now_add=True)),
                ('effective_to', models.DateField(blank=True, null=True)),
                ('agent', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='portfolio', to='accounts.fieldagent')),
                ('council', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='tenancy.council')),
                ('ward', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='agent_portfolio_entries', to='tenancy.wardzone')),
                ('council_revenue_item', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='agent_portfolio_entries', to='revenue.councilrevenueitem')),
            ],
            options={
                'db_table': 'agent_portfolio',
                'ordering': ['-effective_from'],
            },
        ),
        migrations.RunSQL(
            sql=[
                'ALTER TABLE "agent_portfolio" ENABLE ROW LEVEL SECURITY;',
                'ALTER TABLE "agent_portfolio" FORCE ROW LEVEL SECURITY;',
                """
                CREATE POLICY "agent_portfolio_tenant_isolation" ON "agent_portfolio"
                USING (council_id = NULLIF(current_setting('app.council_id', true), '')::integer)
                WITH CHECK (council_id = NULLIF(current_setting('app.council_id', true), '')::integer);
                """,
            ],
            reverse_sql=[
                'DROP POLICY IF EXISTS "agent_portfolio_tenant_isolation" ON "agent_portfolio";',
                'ALTER TABLE "agent_portfolio" NO FORCE ROW LEVEL SECURITY;',
                'ALTER TABLE "agent_portfolio" DISABLE ROW LEVEL SECURITY;',
            ],
        ),
    ]
