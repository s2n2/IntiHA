use intiha_engine::server::{App, Config};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "warn,intiha_engine=info".into()),
        )
        .init();
    let config = Config::from_env()?;
    let clients = tokio::net::TcpListener::bind((config.bind.as_str(), config.client_port)).await?;
    let bridge = tokio::net::TcpListener::bind((config.bind.as_str(), config.bridge_port)).await?;
    let app = App::new(config).await?;
    tracing::info!(
        version = intiha_engine::VERSION,
        "IntiHA engine started; waiting for Home Assistant"
    );
    let supervisor = tokio::spawn(app.clone().supervise());
    let client_app = app.clone();
    let mut client_task =
        tokio::spawn(async move { axum::serve(clients, client_app.client_router()).await });
    let bridge_app = app.clone();
    let mut bridge_task =
        tokio::spawn(async move { axum::serve(bridge, bridge_app.bridge_router()).await });
    let termination = tokio::select! {
        result = &mut client_task => Some(result),
        result = &mut bridge_task => Some(result),
        _ = wait_for_signal() => None,
    };
    supervisor.abort();
    app.shutdown().await;
    client_task.abort();
    bridge_task.abort();
    if let Some(result) = termination {
        result??;
        anyhow::bail!("An IntiHA listener stopped unexpectedly");
    }
    Ok(())
}

async fn wait_for_signal() {
    #[cfg(unix)]
    {
        if let Ok(mut terminate) =
            tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        {
            tokio::select! { _ = tokio::signal::ctrl_c() => {}, _ = terminate.recv() => {} }
            return;
        }
    }
    let _ = tokio::signal::ctrl_c().await;
}
